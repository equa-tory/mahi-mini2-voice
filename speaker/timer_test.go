package main

import (
	"path/filepath"
	"testing"
	"time"
)

func TestTimerDuration(t *testing.T) {
	for _, s := range []string{"2m", "90", "1h30m", "99m59s"} {
		if _, e := parseTimerDuration(s); e != nil {
			t.Fatal(s, e)
		}
	}
	for _, s := range []string{"0", "-1s", "1.5s", "100m", "forever"} {
		if _, e := parseTimerDuration(s); e == nil {
			t.Fatal(s)
		}
	}
}
func TestTimerRestartAndExpiry(t *testing.T) {
	now := time.Now()
	path := filepath.Join(t.TempDir(), "timer.json")
	timer := localTimer{path: path}
	if e := timer.start(2*time.Second, now); e != nil {
		t.Fatal(e)
	}
	if e := timer.start(time.Second, now); e == nil {
		t.Fatal("replaced active timer")
	}
	restored := localTimer{path: path}
	restored.restore(now.Add(time.Second))
	restored.nextBeep = now.Add(time.Hour)
	if text, ringing := restored.display(now.Add(time.Second)); text != "0001" || ringing {
		t.Fatal(text, ringing)
	}
	restored.tick(now.Add(2 * time.Second))
	if text, ringing := restored.display(now.Add(3 * time.Second)); text != "0000" || !ringing {
		t.Fatal(text, ringing)
	}
	afterFire := localTimer{path: path}
	afterFire.restore(now.Add(3 * time.Second))
	afterFire.nextBeep = now.Add(time.Hour)
	if !afterFire.state.Ringing {
		t.Fatal("lost alarm on restart")
	}
	afterFire.tick(now.Add(123 * time.Second))
	if afterFire.snapshot(now)["active"] != false {
		t.Fatal("alert did not expire")
	}
}
func TestTimerBadClockAndSaveFailure(t *testing.T) {
	now := time.Now()
	path := filepath.Join(t.TempDir(), "timer.json")
	timer := localTimer{path: path}
	if e := timer.start(time.Minute, now); e != nil {
		t.Fatal(e)
	}
	restored := localTimer{path: path}
	restored.restore(now.Add(-time.Hour))
	if restored.state.Error == "" || restored.state.Ringing {
		t.Fatal(restored.state)
	}
	if e := restored.clear(); e != nil {
		t.Fatal(e)
	}
	failed := localTimer{path: filepath.Join(t.TempDir(), "missing", "timer")}
	if e := failed.start(time.Second, now); e == nil || !failed.state.Deadline.IsZero() {
		t.Fatal("save failure accepted")
	}
}
func TestTimerMutePriority(t *testing.T) {
	frame, reason := selectRing(ringView{TimerAlert: true, HardwareMuted: true, Scale: 100})
	if reason != "hardware_microphone_muted" || frame != solid([3]int{255, 0, 0}) {
		t.Fatal(frame, reason)
	}
	_, reason = selectRing(ringView{TimerAlert: true, Scale: 100})
	if reason != "timer_alert" {
		t.Fatal(reason)
	}
}

func TestPickerLifecycle(t *testing.T) {
	now := time.Now()
	timer := localTimer{path: filepath.Join(t.TempDir(), "timer.json")}
	if e := timer.pick("pick", now); e != nil {
		t.Fatal(e)
	}
	if text, _ := timer.display(now); text != "   3" {
		t.Fatal(text)
	}
	if timer.snapshot(now)["active"] != false {
		t.Fatal("picker started timer")
	}
	for i := 0; i < 110; i++ {
		timer.pick("increase", now)
	}
	if timer.snapshot(now)["selection_minutes"] != 99 {
		t.Fatal("upper bound")
	}
	for i := 0; i < 110; i++ {
		timer.pick("decrease", now)
	}
	if timer.snapshot(now)["selection_minutes"] != 1 {
		t.Fatal("lower bound")
	}
	restored := localTimer{path: timer.path}
	restored.restore(now)
	if restored.snapshot(now)["selection_minutes"] != 0 {
		t.Fatal("selection persisted")
	}
	timer.pick("pick", now)
	if timer.snapshot(now)["remaining_seconds"] != 60 {
		t.Fatal("confirmation")
	}
	timer.pick("pick", now)
	if timer.snapshot(now)["remaining_seconds"] != 60 {
		t.Fatal("replaced timer")
	}
	restored.restore(now)
	if restored.snapshot(now)["remaining_seconds"] != 60 {
		t.Fatal("confirmed timer not persisted")
	}
}
func TestPickerTimeoutAndConcurrentTimer(t *testing.T) {
	now := time.Now()
	timer := localTimer{path: filepath.Join(t.TempDir(), "timer.json")}
	timer.pick("pick", now)
	timer.touchSelection(now.Add(20 * time.Second))
	if timer.snapshot(now.Add(40 * time.Second))["selection_minutes"] != 3 {
		t.Fatal("input did not renew lease")
	}
	if timer.snapshot(now.Add(51 * time.Second))["selection_minutes"] != 0 {
		t.Fatal("timeout")
	}
	if timer.snapshot(now.Add(51 * time.Second))["active"] != false {
		t.Fatal("timeout started timer")
	}
	timer.pick("pick", now)
	if e := timer.start(10*time.Second, now); e != nil {
		t.Fatal(e)
	}
	if timer.snapshot(now)["selection_minutes"] != 0 {
		t.Fatal("external timer did not close picker")
	}
}
