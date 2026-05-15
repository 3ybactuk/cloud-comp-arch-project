package main

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

type cpuSample struct {
	user, nice, system, idle, iowait, irq, softirq uint64
}

func (s cpuSample) total() uint64 {
	return s.user + s.nice + s.system + s.idle + s.iowait + s.irq + s.softirq
}

func (s cpuSample) idleTime() uint64 { return s.idle + s.iowait }

type Monitor struct {
	prev     []cpuSample
	numCores int
}

func NewMonitor(numCores int) *Monitor {
	return &Monitor{
		prev:     make([]cpuSample, numCores),
		numCores: numCores,
	}
}

// Return CPU utilization for each core [0.0, 1.0].
// First call returns zeros (bootstraps delta).
func (m *Monitor) CoreUtils() ([]float64, error) {
	f, err := os.Open("/proc/stat")
	if err != nil {
		return nil, err
	}
	defer f.Close()

	curr := make([]cpuSample, m.numCores)
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if len(line) < 4 || line[:3] != "cpu" || line[3] == ' ' {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 8 {
			continue
		}
		idx, err := strconv.Atoi(fields[0][3:])
		if err != nil || idx >= m.numCores {
			continue
		}
		parse := func(s string) uint64 { v, _ := strconv.ParseUint(s, 10, 64); return v }
		curr[idx] = cpuSample{
			user: parse(fields[1]), nice: parse(fields[2]),
			system: parse(fields[3]), idle: parse(fields[4]),
			iowait: parse(fields[5]), irq: parse(fields[6]),
			softirq: parse(fields[7]),
		}
	}

	utils := make([]float64, m.numCores)
	for i := 0; i < m.numCores; i++ {
		dt := curr[i].total() - m.prev[i].total()
		di := curr[i].idleTime() - m.prev[i].idleTime()
		if dt > 0 {
			utils[i] = float64(dt-di) / float64(dt)
		}
	}
	m.prev = curr
	return utils, nil
}

func AvgUtil(utils []float64, cores []int) float64 {
	if len(cores) == 0 {
		return 0
	}
	sum := 0.0
	for _, c := range cores {
		sum += utils[c]
	}
	return sum / float64(len(cores))
}

func MemcachedStats(ip string, port int) map[string]string {
	addr := net.JoinHostPort(ip, fmt.Sprintf("%d", port))
	conn, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		return nil
	}
	defer conn.Close()

	conn.SetDeadline(time.Now().Add(3 * time.Second))
	fmt.Fprintln(conn, "stats")

	stats := make(map[string]string)

	sc := bufio.NewScanner(conn)
	for sc.Scan() {
		line := sc.Text()
		if line == "END" {
			break
		}

		if strings.HasPrefix(line, "STAT ") {
			parts := strings.Fields(line)

			if len(parts) == 3 {
				stats[parts[1]] = parts[2]
			}
		}
	}

	return stats
}
