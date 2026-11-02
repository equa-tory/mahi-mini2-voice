package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"os/exec"
	"strconv"
	"sync"
	"time"
)

type timerState struct {
	Started  time.Time `json:"started"`
	Deadline time.Time `json:"deadline"`
	Ringing  bool      `json:"ringing"`
	Error    string    `json:"error,omitempty"`
}
type localTimer struct {
	mu             sync.Mutex
	selection      int
	selectionUntil time.Time
	state          timerState
	path           string
	nextBeep       time.Time
	audioCancel    context.CancelFunc
	audioID        uint64
}

var speakerTimer = localTimer{path: dir + "timer.json"}

func parseTimerDuration(s string) (time.Duration, error) {
	d, e := time.ParseDuration(s)
	if e != nil {
		n, err := strconv.Atoi(s)
		if err != nil {
			return 0, errors.New("duration must be seconds or e.g. 2m, 1h30m")
		}
		d = time.Duration(n) * time.Second
	}
	if d < time.Second || d > 5999*time.Second || d%time.Second != 0 {
		return 0, errors.New("timer duration must be 1..5999 whole seconds (99m59s)")
	}
	return d, nil
}
func (t *localTimer) saveLocked() error {
	b, _ := json.Marshal(t.state)
	return atomicWrite(t.path, string(b))
}
func (t *localTimer) restore(now time.Time) {
	b, e := os.ReadFile(t.path)
	if e != nil {
		return
	}
	var state timerState
	if json.Unmarshal(b, &state) != nil {
		t.mu.Lock()
		t.state.Error = "invalid timer state"
		t.mu.Unlock()
		return
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	t.state = state
	if !state.Deadline.IsZero() && now.Before(state.Started) {
		t.state.Error = "clock moved backwards; cancel and restart timer"
		t.state.Ringing = false
	}
}
func (t *localTimer) start(d time.Duration, now time.Time) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if !t.state.Deadline.IsZero() {
		return errors.New("timer exists; cancel or dismiss it first")
	}
	before := t.state
	t.state = timerState{Started: now, Deadline: now.Add(d)}
	if e := t.saveLocked(); e != nil {
		t.state = before
		return e
	}
	t.selection = 0
	t.selectionUntil = time.Time{}
	t.nextBeep = time.Time{}
	return nil
}
func (t *localTimer) clear() error {
	t.mu.Lock()
	defer t.mu.Unlock()
	before := t.state
	t.state = timerState{}
	if e := t.saveLocked(); e != nil {
		t.state = before
		return e
	}
	t.audioID++
	if t.audioCancel != nil {
		t.audioCancel()
		t.audioCancel = nil
	}
	return nil
}
func (t *localTimer) snapshot(now time.Time) map[string]any {
	t.mu.Lock()
	defer t.mu.Unlock()
	seconds := int(math.Ceil(t.state.Deadline.Sub(now).Seconds()))
	if seconds < 0 || t.state.Deadline.IsZero() {
		seconds = 0
	}
	t.expireSelectionLocked(now)
	return map[string]any{"selection_minutes": t.selection, "selection_until": t.selectionUntil, "active": !t.state.Deadline.IsZero(), "ringing": t.state.Ringing, "remaining_seconds": seconds, "deadline": t.state.Deadline, "error": t.state.Error, "sound_level": timerSoundLevel()}
}
func (t *localTimer) display(now time.Time) (string, bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.expireSelectionLocked(now)
	if t.selection > 0 {
		return fmt.Sprintf("%4d", t.selection), false
	}
	if t.state.Deadline.IsZero() {
		return "", false
	}
	if t.state.Error != "" {
		return "Err", false
	}
	if t.state.Ringing {
		return "0000", true
	}
	n := int(math.Ceil(t.state.Deadline.Sub(now).Seconds()))
	if n < 0 {
		n = 0
	}
	if n > 5999 {
		n = 5999
	}
	return fmt.Sprintf("%02d%02d", n/60, n%60), false
}
func timerIsRinging() bool { _, ringing := speakerTimer.display(time.Now()); return ringing }
func (t *localTimer) tick(now time.Time) {
	t.mu.Lock()
	if t.state.Deadline.IsZero() || t.state.Error != "" {
		t.mu.Unlock()
		return
	}
	if now.Before(t.state.Started) {
		t.state.Error = "clock moved backwards; cancel and restart timer"
		t.mu.Unlock()
		return
	}
	fired := false
	if !now.Before(t.state.Deadline) && !t.state.Ringing {
		t.state.Ringing = true
		fired = true
		if e := t.saveLocked(); e != nil {
			t.state.Error = e.Error()
		}
	}
	if now.Sub(t.state.Deadline) >= 120*time.Second {
		before := t.state
		t.state = timerState{}
		e := t.saveLocked()
		if e != nil {
			t.state = before
			t.state.Error = e.Error()
		}
		if t.audioCancel != nil {
			t.audioCancel()
			t.audioCancel = nil
		}
		t.audioID++
		t.mu.Unlock()
		if e != nil {
			emitFields("", "timer_error", 0, 0, map[string]any{"error": e.Error()})
		} else {
			emit("", "timer_auto_dismissed", 0, 0)
		}
		return
	}
	beep := t.state.Ringing && !now.Before(t.nextBeep)
	var ctx context.Context
	var cancel context.CancelFunc
	var audioID uint64
	if beep {
		t.nextBeep = now.Add(10 * time.Second)
		ctx, cancel = context.WithTimeout(context.Background(), 2*time.Second)
		t.audioCancel = cancel
		t.audioID++
		audioID = t.audioID
	}
	t.mu.Unlock()
	if fired {
		emit("", "timer_fired", 0, 0)
	}
	if beep {
		go func() {
			defer func() {
				cancel()
				t.mu.Lock()
				if t.audioID == audioID {
					t.audioCancel = nil
				}
				t.mu.Unlock()
			}()

			e := playTimerTone(ctx)
			if e != nil && ctx.Err() == nil {
				emitFields("", "timer_audio_error", 0, 0, map[string]any{"error": "alert playback failed"})
			}
		}()
	}
}
func playTimerTone(ctx context.Context) error {
	const rate = 48000
	pcm := make([]byte, rate*4)
	amplitude := 32700 * timerSoundLevel() / 100
	for i := 0; i < rate; i++ {
		seconds := float64(i) / rate
		// Two short, low-amplitude notes; do not touch any mixer setting.
		phase := math.Mod(seconds, .5)
		envelope := 0.0
		if phase < .3 {
			envelope = math.Min(1, phase/.025) * math.Min(1, (.3-phase)/.04)
		}
		value := int16(float64(amplitude) * envelope * math.Sin(2*math.Pi*660*seconds))
		binary.LittleEndian.PutUint16(pcm[i*4:], uint16(value))
		binary.LittleEndian.PutUint16(pcm[i*4+2:], uint16(value))
	}
	c := exec.CommandContext(ctx, "/usr/bin/aplay", "-q", "-D", "default", "-t", "raw", "-f", "S16_LE", "-r", "48000", "-c", "2")
	c.Stdin = bytes.NewReader(pcm)
	return c.Run()
}
func timerCommand(args []string) (any, error, bool) {
	if len(args) == 0 || args[0] != "timer" {
		return nil, nil, false
	}
	if len(args) == 2 && args[1] == "selection" {
		return speakerTimer.snapshot(time.Now()), nil, true
	}
	if len(args) == 2 && (args[1] == "status" || args[1] == "get") {
		return speakerTimer.snapshot(time.Now()), nil, true
	}
	if read(dir+"default") != "local" {
		return nil, errors.New("local mode required"), true
	}
	if len(args) == 2 && (args[1] == "pick" || args[1] == "increase" || args[1] == "decrease" || args[1] == "close") {
		e := speakerTimer.pick(args[1], time.Now())
		if speakerTimer.snapshot(time.Now())["active"] == true {
			text, _ := speakerTimer.display(time.Now())
			overlay(text)
		}
		return speakerTimer.snapshot(time.Now()), e, true
	}
	if len(args) == 3 && args[1] == "sound-level" {
		n, e := integer(args[2], 0, 100)
		if e != nil {
			return nil, e, true
		}
		return "alert level saved", atomicWrite(dir+"timer-sound-level", strconv.Itoa(n)), true
	}
	if len(args) == 2 && args[1] == "sound-test" {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		return "alert test played", playTimerTone(ctx), true
	}
	if len(args) == 3 && args[1] == "start" {
		d, e := parseTimerDuration(args[2])
		if e != nil {
			return nil, e, true
		}
		e = speakerTimer.start(d, time.Now())
		if e == nil {
			emit("", "timer_started", 0, d)
		}
		return speakerTimer.snapshot(time.Now()), e, true
	}
	if len(args) == 2 && (args[1] == "cancel" || args[1] == "dismiss") {
		e := speakerTimer.clear()
		if e == nil {
			emit("", "timer_dismissed", 0, 0)
		}
		return "timer cleared", e, true
	}
	return nil, errors.New("timer start DURATION|status|cancel|dismiss|sound-test|sound-level 0..100"), true
}

// Picker state is deliberately RAM-only and shares the timer lock.
func (t *localTimer) expireSelectionLocked(now time.Time) {
	if t.selection > 0 && (!now.Before(t.selectionUntil) || !t.state.Deadline.IsZero()) {
		t.selection = 0
		t.selectionUntil = time.Time{}
	}
}
func (t *localTimer) pick(operation string, now time.Time) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.expireSelectionLocked(now)
	if !t.state.Deadline.IsZero() {
		return nil
	}
	switch operation {
	case "pick":
		if t.selection == 0 {
			t.selection = 3
		} else {
			before := t.state
			t.state = timerState{Started: now, Deadline: now.Add(time.Duration(t.selection) * time.Minute)}
			if e := t.saveLocked(); e != nil {
				t.state = before
				return e
			}
			t.selection = 0
			t.selectionUntil = time.Time{}
			return nil
		}
	case "increase":
		if t.selection == 0 {
			return nil
		}
		if t.selection < 99 {
			t.selection++
		}
	case "decrease":
		if t.selection == 0 {
			return nil
		}
		if t.selection > 1 {
			t.selection--
		}
	case "close":
		t.selection = 0
		t.selectionUntil = time.Time{}
		return nil
	default:
		return errors.New("unknown picker operation")
	}
	t.selectionUntil = now.Add(30 * time.Second)
	return nil
}
func pickerActive() bool {
	speakerTimer.mu.Lock()
	defer speakerTimer.mu.Unlock()
	speakerTimer.expireSelectionLocked(time.Now())
	return speakerTimer.selection > 0
}

func timerSoundLevel() int {
	n, e := strconv.Atoi(read(dir + "timer-sound-level"))
	if e != nil || n < 0 || n > 100 {
		return 20
	}
	return n
}

func (t *localTimer) touchSelection(now time.Time) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.expireSelectionLocked(now)
	if t.selection > 0 {
		t.selectionUntil = now.Add(30 * time.Second)
	}
}
