package main

import (
	"sync"
	"testing"
	"time"
)

func TestRingPriorityAndBrightness(t *testing.T) {
	manual := solid([3]int{0, 80, 80})
	frameMuted, reasonMuted := selectRing(ringView{Muted: true, Scale: 100})
	if frameMuted != solid([3]int{}) || reasonMuted != "idle" {
		t.Fatal(frameMuted, reasonMuted)
	}
	frame, reason := selectRing(ringView{Idle: [3]int{0, 16, 16}, Scale: 100})
	if frame != solid([3]int{0, 16, 16}) || reason != "idle" {
		t.Fatal(frame, reason)
	}
	_, reason = selectRing(ringView{Manual: &manual, Pairing: true, Scale: 100})
	if reason != "manual" {
		t.Fatal(reason)
	}
	_, reason = selectRing(ringView{Pairing: true, Indicator: "speaking", Scale: 100})
	if reason != "pairing" {
		t.Fatal(reason)
	}
}
func TestDebounce(t *testing.T) {
	start := time.Now()
	d := debouncedSwitch{stable: "0", candidate: "0", since: start}
	if _, changed := d.sample("1", start); !changed {
		t.Fatal("short press lost")
	}
	if _, changed := d.sample("0", start.Add(10*time.Millisecond)); changed {
		t.Fatal("release bounce")
	}
	if _, changed := d.sample("1", start.Add(20*time.Millisecond)); changed {
		t.Fatal("duplicate press during bounce")
	}
	d.sample("0", start.Add(30*time.Millisecond))
	if _, changed := d.sample("0", start.Add(90*time.Millisecond)); !changed {
		t.Fatal("stable release missing")
	}
	if _, changed := d.sample("1", start.Add(time.Second)); !changed {
		t.Fatal("second press missing")
	}
}

func TestPrivacyCoalescesTargets(t *testing.T) {
	entered := make(chan bool, 8)
	release := make(chan struct{})
	var mu sync.Mutex
	var applied []bool
	p := newPrivacy(false, func(target bool) error {
		entered <- target
		if target {
			<-release
		}
		mu.Lock()
		applied = append(applied, target)
		mu.Unlock()
		return nil
	}, nil)
	defer close(p.stop)
	p.request(nil)
	if !<-entered {
		t.Fatal("first request not mute")
	}
	p.request(nil)
	p.request(nil)
	last := p.request(nil) // Latest target = unmuted; intermediate target discarded.
	close(release)
	state, err := p.await(last)
	if err != nil || state.Desired || state.Applied {
		t.Fatal(state, err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(applied) != 2 || !applied[0] || applied[1] {
		t.Fatalf("replayed stale toggles: %v", applied)
	}
}

func TestNativeMuteCannotBeSuppressed(t *testing.T) {
	frame, reason := selectRing(ringView{HardwareMuted: true, Muted: true, Scale: 100})
	if reason != "hardware_microphone_muted" || frame != solid([3]int{255, 0, 0}) {
		t.Fatal(frame, reason)
	}
}
func TestRingFade(t *testing.T) {
	transitionReady = false
	transitionTarget = ringFrame{}
	transitionOutput = ringFrame{}
	transitionFrom = ringFrame{}
	now := time.Now()
	target := solid([3]int{0, 32, 32})
	if f := transitionFrame(target, now); f != solid([3]int{}) {
		t.Fatal(f)
	}
	f := transitionFrame(target, now.Add(200*time.Millisecond))
	if f[0][1] != 16 {
		t.Fatal(f)
	}
	if f = transitionFrame(target, now.Add(time.Second)); f != target {
		t.Fatal(f)
	}
}
func TestMotion(t *testing.T) {
	now := time.Now()
	motionStart = now
	motionColor = [3]int{0, 32, 32}
	for _, kind := range []string{"pulse", "breathe", "chase"} {
		motionName = kind
		if f := motionFrame(now.Add(500 * time.Millisecond)); f == solid([3]int{}) {
			t.Fatal(kind)
		}
	}
	motionName = "pulse"
	if f := motionFrame(now.Add(2 * time.Second)); f != solid([3]int{}) {
		t.Fatal(f)
	}
	motionName = ""
}
