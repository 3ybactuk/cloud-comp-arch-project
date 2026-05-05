package main

import (
	"fmt"
	"net/url"
	"os"
	"strings"
	"time"
)

type JobName string

const (
	JobScheduler     JobName = "scheduler"
	JobMemcached     JobName = "memcached"
	JobBarnes        JobName = "barnes"
	JobBlackscholes  JobName = "blackscholes"
	JobCanneal       JobName = "canneal"
	JobFreqmine      JobName = "freqmine"
	JobRadix         JobName = "radix"
	JobStreamcluster JobName = "streamcluster"
	JobVips          JobName = "vips"
)

const tsFormat = "2006-01-02T15:04:05.000000"

type SchedulerLogger struct {
	file     *os.File
	fileName string
}

func NewSchedulerLogger() *SchedulerLogger {
	fileName := fmt.Sprintf("log%s.txt", time.Now().Format("20060102_150405"))
	f, err := os.Create(fileName)
	if err != nil {
		panic(err)
	}

	l := &SchedulerLogger{file: f, fileName: fileName}
	l.write("start", JobScheduler, "")

	return l
}

func (l *SchedulerLogger) write(event string, job JobName, args string) {
	line := fmt.Sprintf("%s %s %s", time.Now().Format(tsFormat), event, string(job))
	if args != "" {
		line += " " + args
	}

	l.file.WriteString(line + "\n")
	l.file.Sync()
}

func (l *SchedulerLogger) JobStart(job JobName, cores []int, threads int) {
	l.write("start", job, fmt.Sprintf("[%s] %d", intsJoin(cores), threads))
}

func (l *SchedulerLogger) JobEnd(job JobName) {
	l.write("end", job, "")
}

func (l *SchedulerLogger) UpdateCores(job JobName, cores []int) {
	l.write("update_cores", job, fmt.Sprintf("[%s]", intsJoin(cores)))
}

func (l *SchedulerLogger) JobPause(job JobName) {
	l.write("pause", job, "")
}

func (l *SchedulerLogger) JobUnpause(job JobName) {
	l.write("unpause", job, "")
}

func (l *SchedulerLogger) Custom(job JobName, comment string) {
	l.write("custom", job, url.QueryEscape(comment))
}

func (l *SchedulerLogger) End() {
	l.write("end", JobScheduler, "")

	l.file.Sync()
	l.file.Close()
}

func (l *SchedulerLogger) FileName() string { return l.fileName }

func intsJoin(ns []int) string {
	ss := make([]string, len(ns))

	for i, n := range ns {
		ss[i] = fmt.Sprintf("%d", n)
	}

	return strings.Join(ss, ",")
}
