package main

import (
	"context"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCancellationConditionsAndServerExclusions(t *testing.T) {
	for _, name := range []string{"server_demo", "server_example", "server_hello"} {
		if cancellableAction(name, Action{Type: "sequence"}) {
			t.Fatal(name)
		}
	}
	for _, a := range []Action{{Type: "sequence"}, {Type: "bluetooth.pair"}, {Type: "clock", Args: []string{"face", "eyes"}}, {Type: "weather", Args: []string{"show"}}} {
		if !cancellableAction("local", a) {
			t.Fatal(a)
		}
	}
	localPresentationUntil = time.Now().Add(-time.Second)
	if localActionActive() {
		t.Fatal("expired display consumed gesture")
	}
	localPresentationUntil = time.Time{}
}
func TestDelayCancellationStopsSequence(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	started := time.Now()
	if executeWithContext(ctx, Action{Type: "sequence", Steps: []Action{{Type: "delay", Args: []string{"30000"}}}}, 3) != context.Canceled {
		t.Fatal("not canceled")
	}
	if time.Since(started) > 100*time.Millisecond {
		t.Fatal("cancellation waited for delay")
	}
}
func TestDisplayDefaultsAndCustomConfiguration(t *testing.T) {
	c, err := decodeConfiguration(strings.NewReader("version: 1\nactions:\n  face: {type: clock, args: [text, HELO], display_seconds: 7}\n"))
	if err != nil || c.DisplaySeconds != 3 || *c.Actions["face"].DisplaySeconds != 7 {
		t.Fatal(c, err)
	}
	if _, err = decodeConfiguration(strings.NewReader("version: 1\ndisplay_seconds: -1\n")); err == nil {
		t.Fatal("invalid duration accepted")
	}
}

func TestServerPresentationsDoNotCreateLocalCancellation(t *testing.T) {
	configMu.Lock()
	before := runningActions
	runningActions = map[string]string{"remote": "server_hello"}
	configMu.Unlock()
	defer func() { configMu.Lock(); runningActions = before; configMu.Unlock() }()
	localCancel = nil
	localPresentationUntil = time.Time{}
	registerDisplay("clock", 3)
	if localActionActive() {
		t.Fatal("server_hello presentation became cancellable")
	}
}

func TestOrderedCancelTimerThenPicker(t *testing.T) {
	oldConfig := activeConfig
	oldState, oldPath, oldSelection, oldUntil := speakerTimer.state, speakerTimer.path, speakerTimer.selection, speakerTimer.selectionUntil
	defer func() {
		activeConfig = oldConfig
		speakerTimer.state = oldState
		speakerTimer.path = oldPath
		speakerTimer.selection = oldSelection
		speakerTimer.selectionUntil = oldUntil
	}()
	activeConfig = defaults()
	speakerTimer.path = filepath.Join(t.TempDir(), "timer.json")
	speakerTimer.state = timerState{Started: time.Now(), Deadline: time.Now().Add(time.Minute)}
	speakerTimer.selection = 3
	speakerTimer.selectionUntil = time.Now().Add(time.Minute)
	got := nextCancellation()
	if got != "timer.cancel" {
		t.Fatal(got)
	}
	speakerTimer.state = timerState{}
	speakerTimer.selection = 3
	speakerTimer.selectionUntil = time.Now().Add(time.Minute)
	got = nextCancellation()
	if got != "timer.close" {
		t.Fatal(got)
	}

}
