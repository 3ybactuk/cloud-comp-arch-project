package main

import (
	"fmt"
	"os/exec"
	"strconv"
	"strings"
)

type MemcachedManager struct {
	pid int
}

func NewMemcachedManager(pid int) *MemcachedManager {
	return &MemcachedManager{pid: pid}
}

// Pin (taskset) all memcached threads to the given CPU cores.
func (m *MemcachedManager) SetCores(cores []int) error {
	cpuList := coresToList(cores)
	out, err := exec.Command("sudo", "taskset", "-a", "-cp", cpuList, strconv.Itoa(m.pid)).CombinedOutput()
	if err != nil {
		return fmt.Errorf("taskset pid=%d cores=%s: %v — %s", m.pid, cpuList, err, out)
	}

	return nil
}

func GetMemcachedPID() (int, error) {
	out, err := exec.Command("systemctl", "show", "memcached", "--property=MainPID", "--value").Output()
	if err == nil {
		pid, err := strconv.Atoi(strings.TrimSpace(string(out)))
		if err == nil && pid > 0 {
			return pid, nil
		}
	}

	return 0, err
	//
	// out, err = exec.Command("pgrep", "-x", "memcached").Output()
	// if err != nil {
	// 	return 0, fmt.Errorf("cannot find memcached PID: %v", err)
	// }
	//
	// first := strings.SplitN(strings.TrimSpace(string(out)), "\n", 2)[0]
	// return strconv.Atoi(strings.TrimSpace(first))
}

func coresToList(cores []int) string {
	if len(cores) == 0 {
		return ""
	}

	if len(cores) == 1 {
		return strconv.Itoa(cores[0])
	}

	consecutive := true
	for i := 1; i < len(cores); i++ {
		if cores[i] != cores[i-1]+1 {
			consecutive = false
			break
		}
	}

	if consecutive {
		return fmt.Sprintf("%d-%d", cores[0], cores[len(cores)-1])
	}

	parts := make([]string, len(cores))
	for i, c := range cores {
		parts[i] = strconv.Itoa(c)
	}

	return strings.Join(parts, ",")
}
