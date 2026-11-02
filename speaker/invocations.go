package main

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"sync"
	"time"
)

var localInvocationMu sync.Mutex
var localCancel context.CancelFunc
var localInvocationID uint64
var localPresentationUntil time.Time
var localPresentationName string

func displaySeconds() int {
	configMu.RLock()
	defer configMu.RUnlock()
	if activeConfig.DisplaySeconds > 0 {
		return activeConfig.DisplaySeconds
	}
	return 3
}
func cancellableAction(name string, a Action) bool {
	if strings.HasPrefix(name, "server_") || needsServer(a) {
		return false
	}
	return a.Type == "sequence" || a.Type == "bluetooth.pair" || (a.Type == "weather" && len(a.Args) > 0 && a.Args[0] == "show") || (a.Type == "clock" && len(a.Args) > 0 && (a.Args[0] == "face" || a.Args[0] == "text" || a.Args[0] == "draw" || a.Args[0] == "segments"))
}
func registerDisplay(name string, seconds int) {
	localInvocationMu.Lock()
	defer localInvocationMu.Unlock()
	if localCancel != nil {
		return
	}
	localInvocationID = invocationID.Add(1)
	localPresentationName = name
	localPresentationUntil = time.Now().Add(time.Duration(seconds) * time.Second)
}
func localActionActive() bool {
	localInvocationMu.Lock()
	defer localInvocationMu.Unlock()
	if localCancel != nil {
		return true
	}
	if localPresentationName == "pair" {
		stateMu.Lock()
		active := pairing
		stateMu.Unlock()
		return active && time.Now().Before(localPresentationUntil)
	}
	return time.Now().Before(localPresentationUntil)
}
func clearPresentation() {
	localInvocationMu.Lock()
	localPresentationUntil = time.Time{}
	localInvocationMu.Unlock()
}
func cancelLocalAction(id uint64) bool {
	localInvocationMu.Lock()
	if id != 0 && id != localInvocationID {
		localInvocationMu.Unlock()
		return false
	}
	if localCancel == nil && localPresentationUntil.IsZero() {
		localInvocationMu.Unlock()
		return false
	}
	if localCancel != nil {
		localCancel()
	}
	pair := localPresentationName == "pair"
	localCancel = nil
	localPresentationUntil = time.Time{}
	localInvocationMu.Unlock()
	_, _ = dispatch([]string{"clock", "clear"})
	ringMu.Lock()
	motionName = ""
	ringEnabled = false
	ringMu.Unlock()
	if pair {
		stateMu.Lock()
		pairing = false
		stateMu.Unlock()
		_, _ = cmd("/usr/bin/bluetoothctl", "discoverable", "off")
	}
	return true
}
func executeInvocation(job invocation) error {
	if !cancellableAction(job.Name, job.Action) {
		return executeAction(job.Action)
	}
	seconds := displaySeconds()
	if job.Action.DisplaySeconds != nil {
		seconds = *job.Action.DisplaySeconds
	}
	ctx, cancel := context.WithCancel(context.Background())
	if job.Action.Type == "sequence" {
		ctx, cancel = context.WithTimeout(context.Background(), time.Duration(seconds)*time.Second)
	}
	localInvocationMu.Lock()
	localInvocationID = job.ID
	localCancel = cancel
	localPresentationName = job.Name
	if job.Action.Type == "bluetooth.pair" {
		localPresentationName = "pair"
	}
	localPresentationUntil = time.Now().Add(time.Duration(seconds) * time.Second)
	if job.Action.Type == "bluetooth.pair" {
		localPresentationUntil = time.Now().Add(180 * time.Second)
	}
	localInvocationMu.Unlock()
	err := executeWithContext(ctx, job.Action, seconds)
	if job.Action.Type == "sequence" && err == nil {
		select {
		case <-ctx.Done():
			err = ctx.Err()
		case <-time.After(time.Duration(seconds) * time.Second):
		}
	}
	localInvocationMu.Lock()
	if localInvocationID == job.ID && err == nil {
		localCancel = nil
	}
	localInvocationMu.Unlock()
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		// cancelLocalAction only clears the presentation still owned by this invocation.
		cancelLocalAction(job.ID)
		err = nil
	}
	cancel()
	return err
}
func executeWithContext(ctx context.Context, a Action, seconds int) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if a.Type == "sequence" {
		for _, step := range a.Steps {
			if err := executeWithContext(ctx, step, seconds); err != nil {
				return err
			}
		}
		return nil
	}
	if a.Type == "delay" {
		n, _ := strconv.Atoi(a.Args[0])
		timer := time.NewTimer(time.Duration(n) * time.Millisecond)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
			return nil
		}
	}
	a.Args = append([]string(nil), a.Args...)
	if a.Type == "clock" && len(a.Args) > 0 {
		size := map[string]int{"text": 2, "face": 2, "draw": 2, "segments": 5}[a.Args[0]]
		if size > 0 {
			if len(a.Args) == size {
				a.Args = append(a.Args, strconv.Itoa(seconds))
			} else if len(a.Args) == size+1 {
				a.Args[size] = strconv.Itoa(seconds)
			}
		}
	}
	if a.Type == "weather" && len(a.Args) > 0 && a.Args[0] == "show" {
		a.Args = []string{"show", strconv.Itoa(seconds)}
	}
	return executeAction(a)
}
