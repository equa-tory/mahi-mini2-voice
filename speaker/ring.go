package main

import (
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

type ringFrame [6][3]int
type ringView struct {
	HardwareMuted, Unmuting, Muted, Pending, PrivacyError, Pairing, Error, TimerAlert bool
	Idle                                                                              [3]int
	Manual                                                                            *ringFrame
	Indicator                                                                         string
	Scale                                                                             int
	Phase                                                                             float64
}

func solid(rgb [3]int) (out ringFrame) {
	for i := range out {
		out[i] = rgb
	}
	return
}
func selectRing(v ringView) (ringFrame, string) {
	// Native mute red survives LED-controller shutdown: software frames cannot suppress it.
	if v.HardwareMuted {
		return solid([3]int{255, 0, 0}), "hardware_microphone_muted"
	}
	if v.PrivacyError {
		return solid([3]int{24, 8, 0}), "privacy_error"
	}
	frame := solid(v.Idle)
	reason := "idle"
	switch {
	case v.TimerAlert:
		level := 8 + int(24*(1-math.Cos(v.Phase))/2)
		frame = solid([3]int{level, level / 2, 0})
		reason = "timer_alert"
	case v.Error:
		frame = solid([3]int{24, 8, 0})
		reason = "error"
	case v.Manual != nil:
		frame = *v.Manual
		reason = "manual"
	case v.Pairing:
		level := 3 + int(21*(1-math.Cos(v.Phase))/2)
		frame = solid([3]int{0, level / 3, level})
		reason = "pairing"
	default:
		wave := 3 + int(13*(1-math.Cos(v.Phase))/2)
		switch v.Indicator {
		case "listening":
			frame = solid([3]int{0, 8, 24})
		case "thinking":
			frame = solid([3]int{0, wave, wave})
		case "speaking":
			frame = solid([3]int{0, wave, 0})
		case "success":
			frame = solid([3]int{0, 20, 0})
		case "error":
			frame = solid([3]int{24, 8, 0})
		}
		if v.Indicator != "" {
			reason = v.Indicator
		}
	}
	for z := range frame {
		for c := range frame[z] {
			value := frame[z][c] * v.Scale / 100
			if value < 0 {
				value = 0
			}
			if value > 255 {
				value = 255
			}
			frame[z][c] = value
		}
	}
	return frame, reason
}

var ringHealthMu sync.Mutex
var ringReason, ringError string
var ringCorrections uint64
var ringExpected ringFrame
var lastRing ringFrame
var ringValid bool
var lastHardwareMute bool

func ringPaths(z, c int) string {
	return fmt.Sprintf("/sys/class/leds/mini2:%s:ring%d/brightness", []string{"red", "green", "blue"}[c], z+1)
}
func renderRing(view ringView, check bool) {
	frame, reason := selectRing(view)
	if view.HardwareMuted {
		ringHealthMu.Lock()
		ringReason = reason
		ringExpected = frame
		ringError = ""
		ringHealthMu.Unlock()
		ringValid = false
		transitionReady = false
		return
	}
	now := time.Now()
	if view.HardwareMuted != lastHardwareMute {
		ringValid = false
		lastHardwareMute = view.HardwareMuted
	}
	frame = transitionFrame(frame, now)
	mismatch := false
	if check && ringValid {
		for z := range frame {
			for c := range frame[z] {
				if read(ringPaths(z, c)) != strconv.Itoa(lastRing[z][c]) {
					mismatch = true
				}
			}
		}
	}
	ringHealthMu.Lock()
	ringReason = reason
	ringExpected = frame
	if mismatch {
		ringCorrections++
	}
	ringHealthMu.Unlock()
	if ringValid && frame == lastRing && !mismatch {
		return
	}
	// Extinguish unwanted channels before lighting the new ones; no white/red flash.
	var err error
	for _, off := range []bool{true, false} {
		for z := range frame {
			for c, value := range frame[z] {
				if (value == 0) != off {
					continue
				}
				if e := os.WriteFile(ringPaths(z, c), []byte(strconv.Itoa(value)), 0644); e != nil {
					err = e
				}
			}
		}
	}
	ringHealthMu.Lock()
	ringError = ""
	if err != nil {
		ringError = err.Error()
	}
	ringHealthMu.Unlock()
	if err == nil {
		lastRing = frame
		ringValid = true
	}
}
func ringViewNow(now time.Time, ambient int, errorUntil time.Time) ringView {
	p := privacy.snapshot()
	stateMu.Lock()
	pair := pairing
	stateMu.Unlock()
	v := ringView{TimerAlert: timerIsRinging(), HardwareMuted: hardwareMuted(), Unmuting: !p.Desired && p.Applied, Muted: p.Applied || read(dir+"mic-muted") == "1", Pending: p.Desired, PrivacyError: p.Error != "", Pairing: pair, Error: now.Before(errorUntil), Idle: idleColor(), Scale: 100, Phase: float64(now.UnixMilli()%3000) * 2 * math.Pi / 3000}
	ringMu.Lock()
	if motionActive(now) {
		copy := motionFrame(now)
		v.Manual = &copy
	} else if ringEnabled {
		if ringUntil.IsZero() || now.Before(ringUntil) {
			copy := ringFrame(ringOverride)
			v.Manual = &copy
		} else {
			ringEnabled = false
		}
	}
	ringMu.Unlock()
	if read(dir+"ring-auto-brightness") == "1" {
		v.Scale = ambient * 100 / 16
		if v.Scale < 8 {
			v.Scale = 8
		}
		if v.Scale > 100 {
			v.Scale = 100
		}
	} else if n, e := strconv.Atoi(read(dir + "ring-brightness")); e == nil {
		v.Scale = n
	}
	f := strings.Fields(read("/tmp/mini2-indication"))
	if len(f) == 2 {
		expiry, _ := strconv.ParseInt(f[1], 10, 64)
		if now.Unix() < expiry {
			v.Indicator = f[0]
		}
	}
	return v
}
