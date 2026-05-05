package main

import (
	"context"
	"fmt"
	"io"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/client"
)

type DockerManager struct {
	cli *client.Client
}

func NewDockerManager() (*DockerManager, error) {
	cli, err := client.NewClientWithOpts(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		return nil, err
	}

	return &DockerManager{cli: cli}, nil
}

// Creates and starts a batch-job container; returns the container ID.
func (d *DockerManager) StartContainer(name, image string, cmd []string, cores []int) (string, error) {
	ctx := context.Background()
	// Remove leftover container with same name from a previous run.
	d.cli.ContainerRemove(ctx, name, container.RemoveOptions{Force: true})

	resp, err := d.cli.ContainerCreate(ctx,
		&container.Config{
			Image: image,
			Cmd:   cmd,
		},
		&container.HostConfig{
			Resources: container.Resources{CpusetCpus: coresToList(cores)},
		},
		nil, nil, name)
	if err != nil {
		return "", fmt.Errorf("create %s: %w", name, err)
	}

	if err := d.cli.ContainerStart(ctx, resp.ID, container.StartOptions{}); err != nil {
		return "", fmt.Errorf("start %s: %w", name, err)
	}

	return resp.ID, nil
}

// Updates the cpuset of a running container.
func (d *DockerManager) UpdateCores(id string, cores []int) error {
	_, err := d.cli.ContainerUpdate(context.Background(), id, container.UpdateConfig{
		Resources: container.Resources{CpusetCpus: coresToList(cores)},
	})

	return err
}

func (d *DockerManager) Pause(id string) error {
	return d.cli.ContainerPause(context.Background(), id)
}

func (d *DockerManager) Unpause(id string) error {
	return d.cli.ContainerUnpause(context.Background(), id)
}

func (d *DockerManager) Wait(id string) (int64, error) {
	statusCh, errCh := d.cli.ContainerWait(context.Background(), id, container.WaitConditionNotRunning)
	select {
	case err := <-errCh:
		return -1, err
	case s := <-statusCh:
		return s.StatusCode, nil
	}
}

func (d *DockerManager) Remove(id string) {
	d.cli.ContainerRemove(context.Background(), id, container.RemoveOptions{Force: true})
}

func (d *DockerManager) Logs(id string) (string, error) {
	r, err := d.cli.ContainerLogs(context.Background(), id, container.LogsOptions{
		ShowStdout: true, ShowStderr: true,
	})
	if err != nil {
		return "", err
	}

	defer r.Close()

	b, err := io.ReadAll(r)

	return string(b), err
}
