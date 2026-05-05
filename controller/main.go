package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	pid := flag.Int("pid", 0, "memcached PID (0 = auto-detect via systemctl)")
	memIP := flag.String("memcached-ip", "127.0.0.1", "memcached IP address")
	memPort := flag.Int("memcached-port", 11211, "memcached port")
	flag.Parse()

	if *pid == 0 {
		p, err := GetMemcachedPID()
		if err != nil {
			log.Fatalf("cannot find memcached PID: %v", err)
		}
		*pid = p
		log.Printf("auto-detected memcached PID: %d", *pid)
	}

	logger := NewSchedulerLogger()
	log.Printf("logging to %s", logger.FileName())

	sched, err := NewScheduler(*pid, *memIP, *memPort, logger)
	if err != nil {
		log.Fatalf("scheduler init: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-sigs
		log.Println("received signal — stopping")
		cancel()
	}()

	if err := sched.Run(ctx); err != nil {
		log.Printf("scheduler error: %v", err)
	}

	logger.End()
	log.Printf("done. log: %s", logger.FileName())
}
