package main

import (
	"context"
	"fmt"
	"log"
	"time"
)

const (
	totalCores        = 4
	maxMemcachedCores = 3
	minMemcachedCores = 1

	// CPU utilization thresholds for memcached core scaling.
	// 0.60 gives headroom before latency spikes (empirically, the SLO knee occurs
	// before 78% CPU utilisation, so reacting at 60% prevents violations).
	highThreshold = 0.60
	lowThreshold  = 0.25

	// Require lowThreshold to hold for this many consecutive polls before shrinking.
	stablePollsNeeded = 3

	pollInterval = 2 * time.Second
)

type jobSpec struct {
	name    JobName
	image   string
	suite   string // "parsec" or "splash2x"
	program string
}

// sent by a background goroutine when a container exits
type jobResult struct {
	spec     jobSpec
	id       string
	exitCode int64
	err      error
	duration time.Duration
}

// tracks running container
type runningJob struct {
	spec      jobSpec
	id        string
	cores     []int
	threads   int
	startTime time.Time
	paused    bool
}

var defaultQueue = []jobSpec{
	{JobFreqmine, "anakli/cca:parsec_freqmine", "parsec", "freqmine"},
	{JobCanneal, "anakli/cca:parsec_canneal", "parsec", "canneal"},
	{JobBlackscholes, "anakli/cca:parsec_blackscholes", "parsec", "blackscholes"},
	{JobStreamcluster, "anakli/cca:parsec_streamcluster", "parsec", "streamcluster"},
	{JobBarnes, "anakli/cca:splash2x_barnes", "splash2x", "barnes"},
	{JobVips, "anakli/cca:parsec_vips", "parsec", "vips"},
	{JobRadix, "anakli/cca:splash2x_radix", "splash2x", "radix"},
}

// Scheduler orchestrates memcached resource management and batch job execution.
type Scheduler struct {
	docker    *DockerManager
	memcached *MemcachedManager
	logger    *SchedulerLogger
	monitor   *Monitor
	memIP     string
	memPort   int

	memCores  int // current cores allocated to memcached
	pending   []jobSpec
	active    *runningJob // at most one batch job at a time
	completed int
	lowCount  int
	doneCh    chan jobResult
}

func NewScheduler(memPID int, memIP string, memPort int, logger *SchedulerLogger) (*Scheduler, error) {
	docker, err := NewDockerManager()
	if err != nil {
		return nil, fmt.Errorf("docker: %w", err)
	}

	return &Scheduler{
		docker:    docker,
		memcached: NewMemcachedManager(memPID),
		logger:    logger,
		monitor:   NewMonitor(totalCores),
		memIP:     memIP,
		memPort:   memPort,
		memCores:  2,
		pending:   append([]jobSpec{}, defaultQueue...),
		doneCh:    make(chan jobResult, 4),
	}, nil
}

func (s *Scheduler) Run(ctx context.Context) error {
	initialCores := makeCoreSlice(0, s.memCores)
	s.logger.JobStart(JobMemcached, initialCores, s.memCores)
	if err := s.memcached.SetCores(initialCores); err != nil {
		log.Printf("warn: initial taskset: %v", err)
	}

	s.monitor.CoreUtils()
	time.Sleep(pollInterval)

	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			s.cleanup()
			return nil

		case <-ticker.C:
			s.tick()
			if s.allDone() {
				return nil
			}

		case res := <-s.doneCh:
			s.handleDone(res)
			if s.allDone() {
				return nil
			}
		}
	}
}

func (s *Scheduler) tick() {
	utils, err := s.monitor.CoreUtils()
	if err != nil {
		log.Printf("warn: /proc/stat: %v", err)

		return
	}

	memUtil := AvgUtil(utils, makeCoreSlice(0, s.memCores))
	log.Printf("memcached util=%.2f on %d cores; batchCores=%d active=%v pending=%d", memUtil, s.memCores, totalCores-s.memCores, s.active != nil, len(s.pending))

	// Scaling decision
	if memUtil > highThreshold && s.memCores < maxMemcachedCores {
		s.expandMemcached()
		s.lowCount = 0
	} else if memUtil < lowThreshold {
		s.lowCount++

		if s.lowCount >= stablePollsNeeded && s.memCores > minMemcachedCores {
			s.shrinkMemcached()
		}
	} else {
		s.lowCount = 0
	}

	s.tryStart()
}

func (s *Scheduler) expandMemcached() {
	s.memCores++
	newCores := makeCoreSlice(0, s.memCores)

	log.Printf("expanding memcached → %d core(s)", s.memCores)

	if err := s.memcached.SetCores(newCores); err != nil {
		log.Printf("taskset expand: %v", err)

		s.memCores--

		return
	}

	s.logger.UpdateCores(JobMemcached, newCores)
	s.rebalanceBatch()
}

func (s *Scheduler) shrinkMemcached() {
	s.memCores--
	newCores := makeCoreSlice(0, s.memCores)

	s.lowCount = 0
	log.Printf("shrinking memcached → %d core(s)", s.memCores)

	if err := s.memcached.SetCores(newCores); err != nil {
		log.Printf("taskset shrink: %v", err)

		s.memCores++

		return
	}

	s.logger.UpdateCores(JobMemcached, newCores)
	s.rebalanceBatch()
}

func (s *Scheduler) batchCores() []int {
	return makeCoreSlice(s.memCores, totalCores)
}

// update active jobs cpuset after memcached core changes.
func (s *Scheduler) rebalanceBatch() {
	if s.active == nil {
		return
	}

	batch := s.batchCores()
	if len(batch) == 0 {
		// No cores left: pause the batch job temporarily.
		if !s.active.paused {
			log.Printf("pausing %s (no batch cores)", s.active.spec.name)

			if err := s.docker.Pause(s.active.id); err != nil {
				log.Printf("pause: %v", err)
			} else {
				s.active.paused = true
				s.logger.JobPause(s.active.spec.name)
			}
		}

		return
	}

	// Unpause if previously paused.
	if s.active.paused {
		log.Printf("unpausing %s", s.active.spec.name)

		if err := s.docker.Unpause(s.active.id); err != nil {
			log.Printf("unpause: %v", err)
		} else {
			s.active.paused = false
			s.logger.JobUnpause(s.active.spec.name)
		}
	}

	// Update cpuset.
	if !coresEq(s.active.cores, batch) {
		if err := s.docker.UpdateCores(s.active.id, batch); err != nil {
			log.Printf("update cores %s: %v", s.active.spec.name, err)
		} else {
			s.active.cores = batch
			s.logger.UpdateCores(s.active.spec.name, batch)
		}
	}
}

func (s *Scheduler) tryStart() {
	if s.active != nil || len(s.pending) == 0 {
		return
	}

	batch := s.batchCores()
	if len(batch) == 0 {
		return
	}

	spec := s.pending[0]
	s.pending = s.pending[1:]
	threads := len(batch)

	cmd := []string{"/bin/sh", "-c", fmt.Sprintf(
		"./run -a run -S %s -p %s -i native -n %d",
		spec.suite, spec.program, threads,
	)}

	name := fmt.Sprintf("parsec-%s", spec.program)

	log.Printf("starting %s cores=%v threads=%d", spec.name, batch, threads)

	id, err := s.docker.StartContainer(name, spec.image, cmd, batch)
	if err != nil {
		log.Printf("failed to start %s: %v — re-queuing", spec.name, err)
		s.pending = append([]jobSpec{spec}, s.pending...)

		return
	}

	s.active = &runningJob{
		spec: spec, id: id,
		cores: batch, threads: threads,
		startTime: time.Now(),
	}
	s.logger.JobStart(spec.name, batch, threads)

	go func(j runningJob) {
		start := time.Now()
		code, err := s.docker.Wait(j.id)
		s.doneCh <- jobResult{
			spec: j.spec, id: j.id,
			exitCode: code, err: err,
			duration: time.Since(start),
		}
	}(*s.active)
}

func (s *Scheduler) handleDone(res jobResult) {
	if res.err != nil {
		log.Printf("job %s wait error: %v", res.spec.name, res.err)
	} else if res.exitCode != 0 {
		log.Printf("job %s exited with code %d", res.spec.name, res.exitCode)
	}

	log.Printf("job %s finished in %v", res.spec.name, res.duration)

	s.logger.JobEnd(res.spec.name)
	s.completed++
	s.docker.Remove(res.id)
	s.active = nil

	s.tryStart()
}

func (s *Scheduler) allDone() bool {
	return len(s.pending) == 0 && s.active == nil
}

func (s *Scheduler) cleanup() {
	if s.active == nil {
		return
	}

	log.Println("shutting down — removing active container")

	if s.active.paused {
		s.docker.Unpause(s.active.id)
	}

	s.docker.Remove(s.active.id)
}

func makeCoreSlice(from, to int) []int {
	if to <= from {
		return nil
	}

	s := make([]int, to-from)
	for i := range s {
		s[i] = from + i
	}

	return s
}

func coresEq(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}

	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}

	return true
}
