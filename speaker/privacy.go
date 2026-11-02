package main

import (
	"errors"
	"fmt"
	"sync"
	"time"
)

// Serialized application of the latest target, never a backlog of toggles.
type privacySnapshot struct {
	Desired   bool   `json:"desired_muted"`
	Applied   bool   `json:"applied_muted"`
	Revision  uint64 `json:"revision"`
	Completed uint64 `json:"completed"`
	Busy      bool   `json:"busy"`
	Error     string `json:"error,omitempty"`
}
type privacyController struct {
	mu      sync.Mutex
	state   privacySnapshot
	wake    chan struct{}
	changed chan struct{}
	stop    chan struct{}
	apply   func(bool) error
	notify  func(bool)
}

func newPrivacy(initial bool, apply func(bool) error, notify func(bool)) *privacyController {
	p := &privacyController{state: privacySnapshot{Desired: initial, Applied: initial}, wake: make(chan struct{}, 1), changed: make(chan struct{}), stop: make(chan struct{}), apply: apply, notify: notify}
	go p.run()
	return p
}
func (p *privacyController) snapshot() privacySnapshot {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.state
}
func (p *privacyController) signal() { close(p.changed); p.changed = make(chan struct{}) }
func (p *privacyController) request(target *bool) uint64 {
	p.mu.Lock()
	if target == nil {
		p.state.Desired = !p.state.Desired
	} else {
		p.state.Desired = *target
	}
	p.state.Revision++
	revision := p.state.Revision
	p.signal()
	p.mu.Unlock()
	select {
	case p.wake <- struct{}{}:
	default:
	}
	return revision
}
func (p *privacyController) await(revision uint64) (privacySnapshot, error) {
	timer := time.NewTimer(15 * time.Second)
	defer timer.Stop()
	for {
		p.mu.Lock()
		state := p.state
		ch := p.changed
		p.mu.Unlock()
		if state.Completed >= revision {
			if state.Error != "" {
				return state, errors.New(state.Error)
			}
			return state, nil
		}
		select {
		case <-ch:
		case <-timer.C:
			return state, errors.New("microphone operation still pending")
		}
	}
}
func (p *privacyController) run() {
	for {
		select {
		case <-p.stop:
			return
		case <-p.wake:
		}
		for {
			p.mu.Lock()
			target := p.state.Desired
			revision := p.state.Revision
			before := p.state.Applied
			need := target != before || p.state.Error != ""
			p.state.Busy = need
			p.signal()
			p.mu.Unlock()
			var err error
			if need {
				err = p.apply(target)
			}
			p.mu.Lock()
			if err == nil {
				p.state.Applied = target
				p.state.Error = ""
			} else {
				p.state.Error = err.Error()
			}
			p.state.Completed = revision
			p.state.Busy = false
			again := revision != p.state.Revision
			p.signal()
			p.mu.Unlock()
			if need && err == nil && before != target && p.notify != nil {
				p.notify(target)
			}
			if !again {
				break
			}
		}
	}
}

func hardwareMuted() bool { return read("/sys/class/gpio/gpio414/value") == "0" }

var privacy *privacyController

func setupPrivacy() {
	privacy = newPrivacy(read(dir+"mic-muted") == "1", func(muted bool) error {
		if !muted && hardwareMuted() {
			return errors.New("rear mute latch is active; press the rear button to unmute")
		}
		action := "unmute"
		if muted {
			action = "mute"
		}
		out, err := cmd("/data/opt/bin/mini2-mic", "--raw", action)
		if err != nil {
			return fmt.Errorf("microphone gate: %v: %s", err, out)
		}
		if (read(dir+"mic-muted") == "1") != muted {
			return errors.New("microphone gate did not confirm requested state")
		}
		return nil
	}, func(muted bool) {
		emit("mute", "privacy_changed", 0, 0)
		if muted {
			overlay("OFF")
		} else {
			overlay("ON")
		}
	})
}

type debouncedSwitch struct {
	stable, candidate string
	since             time.Time
}

func (d *debouncedSwitch) sample(raw string, now time.Time) (string, bool) {
	if raw != "0" && raw != "1" {
		return d.stable, false
	}
	if raw != d.candidate {
		d.candidate = raw
		d.since = now
	}
	delay := 50 * time.Millisecond
	if raw == "1" {
		delay = 0
	}
	if raw != d.stable && now.Sub(d.since) >= delay {
		d.stable = raw
		return raw, true
	}
	return d.stable, false
}
