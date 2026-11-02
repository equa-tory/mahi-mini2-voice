package main

import (
	"errors"
	"math"
	"strconv"
	"time"
)

var transitionTarget, transitionFrom, transitionOutput ringFrame
var transitionStart time.Time
var transitionReady bool

// A single renderer interpolates toward the latest target instead of queueing colors.
func transitionFrame(target ringFrame, now time.Time) ringFrame {
	if !transitionReady {
		transitionStart = now
		transitionReady = true
	}
	if target != transitionTarget {
		transitionFrom = transitionOutput
		transitionTarget = target
		transitionStart = now
	}
	t := float64(now.Sub(transitionStart)) / float64(400*time.Millisecond)
	if t > 1 {
		t = 1
	}
	if t < 0 {
		t = 0
	}
	t = t * t * (3 - 2*t)
	for z := range target {
		for c := range target[z] {
			transitionOutput[z][c] = int(math.Round(float64(transitionFrom[z][c]) + (float64(target[z][c]-transitionFrom[z][c]))*t))
		}
	}
	return transitionOutput
}

var motionName string
var motionStart, motionEnd time.Time
var motionColor = [3]int{0, 32, 32}

func startMotion(name string, seconds int) {
	configMu.RLock()
	color := activeConfig.Active
	configMu.RUnlock()
	if color == [3]int{} {
		color = [3]int{0, 32, 32}
	}
	ringMu.Lock()
	defer ringMu.Unlock()
	// An explicitly set color is the current color; otherwise use active_rgb.
	if ringEnabled {
		color = ringOverride[0]
	}
	motionName = name
	motionColor = color
	motionStart = time.Now()
	motionEnd = motionStart.Add(time.Duration(seconds) * time.Second)
}
func motionActive(now time.Time) bool { return motionName != "" && now.Before(motionEnd) }
func motionFrame(now time.Time) (out ringFrame) {
	age := now.Sub(motionStart).Seconds()
	level := 1.0
	switch motionName {
	case "pulse": // Fade in, hold for a second, fade out.
		if age < .4 {
			level = age / .4
		} else if age > 1.4 {
			level = math.Max(0, 1-(age-1.4)/.6)
		}
	case "breathe":
		level = (1 - math.Cos(age*math.Pi)) / 2
	case "chase":
		for z := range out {
			distance := math.Mod(float64(z)-age*3+60, 6)
			v := math.Max(0, 1-distance/2)
			for c := range out[z] {
				out[z][c] = int(float64(motionColor[c]) * v)
			}
		}
		return
	}
	for z := range out {
		for c := range out[z] {
			out[z][c] = int(float64(motionColor[c]) * level)
		}
	}
	return
}
func motionCommand(args []string) (any, error, bool) {
	if len(args) < 2 || args[0] != "ring" || args[1] != "animate" {
		return nil, nil, false
	}
	if read(dir+"default") != "local" {
		return nil, errors.New("local mode required"), true
	}
	if len(args) != 4 {
		return nil, errors.New("ring animate pulse|breathe|chase seconds"), true
	}
	if args[2] != "pulse" && args[2] != "breathe" && args[2] != "chase" {
		return nil, errors.New("unknown animation"), true
	}
	n, e := strconv.Atoi(args[3])
	if e != nil || n < 1 || n > 86400 {
		return nil, errors.New("seconds must be 1..86400"), true
	}
	startMotion(args[2], n)
	return "animation started", nil, true
}
