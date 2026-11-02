package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

const socketPath = "/tmp/mini2-controls.sock"

var clockMu sync.Mutex
var userText, actionText string
var userUntil, actionUntil time.Time
var clockLast string
var clockRendered string
var clockRenderedPatterns [4]string
var volumeLast string
var volumeUntil time.Time
var subsMu sync.Mutex
var subs = map[chan []byte]bool{}
var seq uint64
var pressed = map[string]bool{"center": false, "mute": false, "plus": false, "minus": false}
var stateMu sync.Mutex
var sensorValue string
var pairing bool
var currentVolume int
var telemetryError string
var ringMu sync.Mutex
var ringOverride [6][3]int
var ringUntil time.Time
var ringEnabled bool

// One ASCII rune per physical digit. Shapes are explicit approximations.
var font = map[rune]string{
	'0': "ABCDEF", '1': "BC", '2': "ABDEG", '3': "ABCDG", '4': "BCFG", '5': "ACDFG", '6': "ACDEFG", '7': "ABC", '8': "ABCDEFG", '9': "ABCDFG",
	'A': "ABCEFG", 'B': "CDEFG", 'C': "ADEF", 'D': "BCDEG", 'E': "ADEFG", 'F': "AEFG", 'G': "ACDEF", 'H': "BCEFG", 'I': "BC", 'J': "BCDE", 'L': "DEF", 'N': "CEG", 'O': "ABCDEF", 'P': "ABEFG", 'Q': "ABCFG", 'R': "EG", 'S': "ACDFG", 'T': "DEFG", 'U': "BCDEF", 'Y': "BCDFG", 'Z': "ABDEG",
	' ': "", '-': "G", '_': "D", '=': "DG", '[': "ADEF", ']': "ABCD", '?': "ABEG", '!': "BC",
}

func validateText(s string) error {
	if len([]rune(s)) > 4 {
		return errors.New("clock accepts at most 4 characters")
	}
	for _, c := range strings.ToUpper(s) {
		if _, ok := font[c]; !ok {
			return fmt.Errorf("unsupported clock character %q; run mini2ctl clock chars", c)
		}
	}
	return nil
}
func overlay(s string) {
	clockMu.Lock()
	actionText = s
	actionUntil = time.Now().Add(1500 * time.Millisecond)
	clockMu.Unlock()
}
func renderClock(now time.Time) {
	if read(dir+"default") != "local" {
		clockLast = ""
		return
	}
	clockMu.Lock()
	defer clockMu.Unlock()
	text := now.In(time.FixedZone("MSK", 10800)).Format("1504")
	timerText, timerRinging := speakerTimer.display(now)
	if timerText != "" {
		text = timerText
	}
	if userText != "" && (userUntil.IsZero() || now.Before(userUntil)) {
		text = userText
	}
	if timerRinging {
		text = timerText
	}
	if now.Before(actionUntil) {
		text = actionText
	}
	if now.Before(volumeUntil) {
		text = fmt.Sprintf("%4s", volumeLast)
	}
	level := read("/tmp/mini2-effective-brightness")
	if level == "" {
		level = "5"
	}
	patterns := displayPatterns(now, text)
	if pickerActive() {
		text, _ = speakerTimer.display(now)
		patterns = [4]string{}
		for i, c := range text {
			if i < 4 {
				patterns[i] = font[c]
			}
		}
	}
	key := strings.Join(patterns[:], ",") + ":" + level
	if key == clockLast {
		return
	}
	files, _ := filepath.Glob("/sys/class/leds/mini2:white:clock*/brightness")
	for _, f := range files {
		write(f, "0")
	}
	for i, pattern := range patterns {
		for _, seg := range pattern {
			write(fmt.Sprintf("/sys/class/leds/mini2:white:clock%c%d/brightness", seg, i+1), level)
		}
	}
	clockLast = key
	clockRendered = text
	clockRenderedPatterns = patterns
}
func emit(button, kind string, count int, duration time.Duration) {
	emitFields(button, kind, count, duration, nil)
}
func emitFields(button, kind string, count int, duration time.Duration, fields map[string]any) {
	if kind == "press" {
		speakerTimer.touchSelection(time.Now())
	}
	if kind == "press" || kind == "release" || kind == "repeat" {
		triggerBinding(button, kind, count)
	}
	stateMu.Lock()
	if kind == "press" {
		pressed[button] = true
	}
	if kind == "release" {
		pressed[button] = false
	}
	stateMu.Unlock()
	subsMu.Lock()
	defer subsMu.Unlock()
	seq++
	v := map[string]any{"seq": seq, "time": time.Now().UTC().Format(time.RFC3339Nano), "button": button, "event": kind, "count": count, "duration_ms": duration.Milliseconds()}
	for k, value := range fields {
		v[k] = value
	}
	if kind == "privacy_changed" {
		v["muted"] = read(dir+"mic-muted") == "1"
	}
	b, _ := json.Marshal(v)
	b = append(b, '\n')
	// RAM-only bounded diagnostic journal; slow subscribers are disconnected.
	if info, e := os.Stat("/tmp/mini2-events.jsonl"); e == nil && info.Size() > 65536 {
		os.Rename("/tmp/mini2-events.jsonl", "/tmp/mini2-events.previous.jsonl")
	}
	if f, e := os.OpenFile("/tmp/mini2-events.jsonl", os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600); e == nil {
		f.Write(b)
		f.Close()
	}
	for ch := range subs {
		select {
		case ch <- b:
		default:
			close(ch)
			delete(subs, ch)
		}
	}
}

type gesture struct {
	down        time.Time
	lastRelease time.Time
	taps        int
	held        bool
	nextRepeat  time.Time
}

func (g *gesture) input(button string, v int, now time.Time) {
	if v == 2 {
		return
	}
	if v == 1 {
		if !g.down.IsZero() {
			return
		}
		g.down = now
		configMu.RLock()
		delay := activeConfig.RepeatDelay
		configMu.RUnlock()
		if delay == 0 {
			delay = 500
		}
		g.nextRepeat = now.Add(time.Duration(delay) * time.Millisecond)
		g.held = false
		emit(button, "press", 0, 0)
		return
	}
	if v != 0 || g.down.IsZero() {
		return
	}
	duration := now.Sub(g.down)
	g.down = time.Time{}
	emit(button, "release", 0, duration)
	if !g.held {
		g.taps++
		g.lastRelease = now
	}
}
func (g *gesture) tick(button string, now time.Time, action func(string, int)) {
	if (button == "plus" || button == "minus") && !g.down.IsZero() && !now.Before(g.nextRepeat) {
		emit(button, "repeat", 0, now.Sub(g.down))
		configMu.RLock()
		interval := activeConfig.RepeatInterval
		configMu.RUnlock()
		if interval == 0 {
			interval = 150
		}
		g.nextRepeat = now.Add(time.Duration(interval) * time.Millisecond)
	}
	if !g.down.IsZero() && !g.held && now.Sub(g.down) >= 3*time.Second {
		g.held = true
		g.taps = 0
		emit(button, "hold", 0, now.Sub(g.down))
		action("hold", 0)
	}
	if g.down.IsZero() && g.taps > 0 && now.Sub(g.lastRelease) >= 350*time.Millisecond {
		n := g.taps
		g.taps = 0
		kind := "tap"
		if n > 1 {
			kind = "multi_tap"
		}
		emit(button, kind, n, 0)
		action(kind, n)
	}
}
func atomicWrite(p, s string) error {
	f, e := os.CreateTemp(filepath.Dir(p), ".mini2-config-*")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	if _, e = f.WriteString(s); e != nil {
		f.Close()
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	return os.Rename(f.Name(), p)
}

func integer(s string, min, max int) (int, error) {
	v, e := strconv.Atoi(s)
	if e != nil || v < min || v > max {
		return 0, fmt.Errorf("expected integer %d..%d", min, max)
	}
	return v, nil
}
func controlsState() map[string]any {
	stateMu.Lock()
	defer stateMu.Unlock()
	clockMu.Lock()
	defer clockMu.Unlock()
	ringMu.Lock()
	defer ringMu.Unlock()
	channels := make([][3]int, 6)
	for z := range channels {
		for i, c := range []string{"red", "green", "blue"} {
			channels[z][i], _ = strconv.Atoi(read(fmt.Sprintf("/sys/class/leds/mini2:%s:ring%d/brightness", c, z+1)))
		}
	}
	ringHealthMu.Lock()
	reason, health, corrections, expected := ringReason, ringError, ringCorrections, ringExpected
	ringHealthMu.Unlock()
	owner := "local"
	if reason == "hardware_microphone_muted" {
		owner = "native_mute"
	}
	keys := map[string]bool{}
	for k, v := range pressed {
		keys[k] = v
	}
	return map[string]any{"voice_owner": leaseSnapshot(), "timer": speakerTimer.snapshot(time.Now()), "configuration": configurationStatus(), "hardware_mic_muted": hardwareMuted(), "mute_hold_supported": false, "ring_expected_rgb": expected, "ring_driver_rgb": channels, "privacy": privacy.snapshot(), "ring_owner": owner, "ring_reason": reason, "ring_error": health, "ring_corrections": corrections, "mode": read(dir + "default"), "volume_percent": currentVolume, "mic_muted": read(dir+"mic-muted") == "1", "mute_button_pressed": read("/sys/class/gpio/gpio419/value") == "1", "mute_latch": read("/sys/class/gpio/gpio414/value"), "buttons": keys, "button_actions": read(dir+"button-actions") != "0", "light_input": sensorValue, "light_raw": read("/sys/bus/iio/devices/iio:device0/in_intensity_raw"), "clock_auto": read(dir+"auto-brightness") == "1", "clock_brightness": read("/tmp/mini2-effective-brightness"), "clock_text": userText, "clock_frame": clockRendered, "clock_segments": clockRenderedPatterns, "ring_auto": read(dir+"ring-auto-brightness") == "1", "ring_brightness": read(dir + "ring-brightness"), "clock_until": userUntil, "ring_rgb": channels, "ring_override": ringEnabled, "pairing": pairing, "telemetry_error": telemetryError}
}
func dispatch(args []string) (any, error) {
	if v, e, ok := extension(args); ok {
		return v, e
	}
	if len(args) == 0 || args[0] == "status" {
		return controlsState(), nil
	}
	if args[0] == "clock" && len(args) == 2 && args[1] == "chars" {
		return map[string]any{"characters": "0123456789 ABCDEFGHIJLNOPQRSTUYZ-_=[]?!", "lowercase": "accepted; normalized to uppercase shapes", "approximations": "B=b D=d N=n R=r T=t; ! resembles 1; ? has no dot; O resembles 0; S resembles 5; Z resembles 2", "unsupported": "K M V W X, Unicode, punctuation outside the listed set", "digits": 4}, nil
	}
	if read(dir+"default") != "local" {
		return nil, errors.New("controls are writable only in local mode")
	}
	switch args[0] {
	case "clock":
		if len(args) == 2 && args[1] == "clear" {
			clockMu.Lock()
			userText = ""
			customFace = nil
			userUntil = time.Time{}
			actionUntil = time.Time{}
			clockLast = ""
			clockMu.Unlock()
			clearPresentation()
			return "clock restored", nil
		}
		if len(args) >= 3 && len(args) <= 4 && args[1] == "text" {
			s := args[2]
			if e := validateText(s); e != nil {
				return nil, e
			}
			seconds := displaySeconds()
			if len(args) == 4 {
				v, e := integer(args[3], 0, 86400)
				if e != nil {
					return nil, e
				}
				seconds = v
			}
			clockMu.Lock()
			userText = s
			customFace = nil
			userUntil = time.Time{}
			if seconds > 0 {
				userUntil = time.Now().Add(time.Duration(seconds) * time.Second)
			}
			clockMu.Unlock()
			registerDisplay("clock", seconds)
			return "text set", nil
		}
	case "brightness":
		if len(args) == 3 && (args[1] == "clock" || args[1] == "ring") {
			target := args[1]
			value := args[2]
			autoFile := dir + "auto-brightness"
			max := 80
			manual := dir + "clock-brightness"
			if target == "ring" {
				autoFile = dir + "ring-auto-brightness"
				max = 100
				manual = dir + "ring-brightness"
			}
			if value == "auto" {
				return "automatic brightness", atomicWrite(autoFile, "1")
			}
			v, e := integer(value, 0, max)
			if e != nil {
				return nil, e
			}
			if e = atomicWrite(manual, strconv.Itoa(v)); e != nil {
				return nil, e
			}
			return "manual brightness", atomicWrite(autoFile, "0")
		}
	case "light":
		if len(args) == 2 && args[1] == "get" {
			return map[string]string{"input": read("/sys/bus/iio/devices/iio:device0/in_intensity_input"), "raw": read("/sys/bus/iio/devices/iio:device0/in_intensity_raw"), "enabled": read("/sys/bus/iio/devices/iio:device0/in_intensity_en"), "driver": "ltr308als01", "gain": read("/sys/bus/iio/devices/iio:device0/in_intensity_hardwaregain"), "gain_choices": read("/sys/bus/iio/devices/iio:device0/hardwaregain_available"), "integration": read("/sys/bus/iio/devices/iio:device0/in_intensity_integration_time"), "integration_choices": read("/sys/bus/iio/devices/iio:device0/integration_time_available")}, nil
		}
		if len(args) == 3 {
			sensor := "/sys/bus/iio/devices/iio:device0/"
			switch args[1] {
			case "enable":
				value := ""
				if args[2] == "on" {
					value = "1"
				}
				if args[2] == "off" {
					value = "0"
				}
				if value == "" {
					return nil, errors.New("light enable expects on or off")
				}
				return "sensor state changed", os.WriteFile(sensor+"in_intensity_en", []byte(value), 0644)
			case "gain", "integration":
				file := "in_intensity_hardwaregain"
				available := "hardwaregain_available"
				if args[1] == "integration" {
					file = "in_intensity_integration_time"
					available = "integration_time_available"
				}
				valid := false
				for _, v := range strings.Fields(read(sensor + available)) {
					if v == args[2] {
						valid = true
					}
				}
				if !valid {
					return nil, fmt.Errorf("allowed values: %s", read(sensor+available))
				}
				return "sensor setting changed", os.WriteFile(sensor+file, []byte(args[2]), 0644)
			}
		}

	case "volume":
		if len(args) == 2 && args[1] == "get" {
			stateMu.Lock()
			v := currentVolume
			stateMu.Unlock()
			return v, nil
		}
		if len(args) == 2 && (args[1] == "up" || args[1] == "down") {
			step := "2%+"
			if args[1] == "down" {
				step = "2%-"
			}
			_, e := cmd("amixer", "-q", "sset", "Master", step)
			return "volume changed", e
		}
		if len(args) == 3 && args[1] == "set" {
			v, e := integer(args[2], 0, 100)
			if e != nil {
				return nil, e
			}
			_, e = cmd("amixer", "-q", "sset", "Master", fmt.Sprintf("%d%%", v))
			return "volume set", e
		}
	case "mute":
		if len(args) == 2 {
			if args[1] == "status" {
				return privacy.snapshot(), nil
			}
			var target *bool
			switch args[1] {
			case "toggle":
			case "mute":
				v := true
				target = &v
			case "unmute":
				v := false
				target = &v
			case "restore":
				v := read(dir+"mic-muted") == "1"
				target = &v
			default:
				return nil, errors.New("invalid mute action")
			}
			if target == nil {
				v := !privacy.snapshot().Desired
				target = &v
			}
			if !*target && hardwareMuted() {
				return nil, errors.New("rear mute latch is active; press rear button to unmute")
			}
			revision := privacy.request(target)
			return privacy.await(revision)
		}
	case "buttons":
		if len(args) == 2 && (args[1] == "on" || args[1] == "off") {
			value := "1"
			if args[1] == "off" {
				value = "0"
			}
			return "default actions changed; microphone privacy stays active", atomicWrite(dir+"button-actions", value)
		}
	case "ring":
		if len(args) == 2 && args[1] == "clear" {
			ringMu.Lock()
			ringEnabled = false
			motionName = ""
			ringMu.Unlock()
			return "idle ring restored", nil
		}
		if len(args) >= 5 && (args[1] == "set" || args[1] == "zone") {
			start := 2
			zone := -1
			if args[1] == "zone" {
				if len(args) < 6 {
					return nil, errors.New("ring zone requires zone R G B [seconds]")
				}
				v, e := integer(args[2], 1, 6)
				if e != nil {
					return nil, e
				}
				zone = v - 1
				start = 3
			}
			if len(args) != start+3 && len(args) != start+4 {
				return nil, errors.New("ring requires R G B [seconds]")
			}
			rgb := [3]int{}
			for i := range rgb {
				v, e := integer(args[start+i], 0, 255)
				if e != nil {
					return nil, e
				}
				rgb[i] = v
			}
			seconds := displaySeconds()
			if len(args) == start+4 {
				v, e := integer(args[start+3], 0, 86400)
				if e != nil {
					return nil, e
				}
				seconds = v
			}
			ringMu.Lock()
			defer ringMu.Unlock()
			motionName = ""
			if !ringEnabled || (!ringUntil.IsZero() && time.Now().After(ringUntil)) {
				for z := range ringOverride {
					for i, c := range []string{"red", "green", "blue"} {
						ringOverride[z][i], _ = strconv.Atoi(read(fmt.Sprintf("/sys/class/leds/mini2:%s:ring%d/brightness", c, z+1)))
					}
				}
			}
			if zone < 0 {
				for z := range ringOverride {
					ringOverride[z] = rgb
				}
			} else {
				ringOverride[zone] = rgb
			}
			ringEnabled = true
			ringUntil = time.Time{}
			if seconds > 0 {
				ringUntil = time.Now().Add(time.Duration(seconds) * time.Second)
			}
			return "ring override set; mute still takes priority", nil
		}
	}
	return nil, errors.New("invalid command; run mini2ctl help")
}
func serveControls() {
	os.Remove(socketPath)
	listener, e := net.Listen("unix", socketPath)
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		return
	}
	os.Chmod(socketPath, 0600)
	for {
		conn, e := listener.Accept()
		if e != nil {
			return
		}
		go func(c net.Conn) {
			defer c.Close()
			c.SetReadDeadline(time.Now().Add(5 * time.Second))
			scan := bufio.NewScanner(c)
			scan.Buffer(make([]byte, 1024), 262144)
			if !scan.Scan() {
				return
			}
			var req struct {
				Args []string `json:"args"`
			}
			if e := json.Unmarshal(scan.Bytes(), &req); e != nil {
				json.NewEncoder(c).Encode(map[string]any{"ok": false, "error": e.Error()})
				return
			}
			if len(req.Args) == 1 && req.Args[0] == "events" {
				ch := make(chan []byte, 64)
				subsMu.Lock()
				subs[ch] = true
				subsMu.Unlock()
				defer func() {
					subsMu.Lock()
					if subs[ch] {
						delete(subs, ch)
						close(ch)
					}
					subsMu.Unlock()
				}()
				for b := range ch {
					c.SetWriteDeadline(time.Now().Add(5 * time.Second))
					if _, e := c.Write(b); e != nil {
						return
					}
				}
				return
			}
			data, e := dispatch(req.Args)
			reply := map[string]any{"ok": e == nil}
			if e != nil {
				reply["error"] = e.Error()
			} else {
				reply["data"] = data
			}
			c.SetWriteDeadline(time.Now().Add(5 * time.Second))
			json.NewEncoder(c).Encode(reply)
		}(conn)
	}
}

const help = `mini2ctl status
mini2ctl clock text HELO [seconds]   # 0/omitted = until clear/reboot
mini2ctl weather show [seconds]
mini2ctl weather status|refresh
mini2ctl timer start DURATION          # e.g. 30s, 2m, 1h; max 99m59s
mini2ctl timer status|cancel|dismiss
mini2ctl timer pick|increase|decrease|close|selection
mini2ctl timer sound-test
mini2ctl timer sound-level 0..100       # alert signal level; default20
mini2ctl clock clear
mini2ctl clock template                 # editable 3-line drawing
mini2ctl clock draw --stdin [seconds]   # read a drawing from stdin
mini2ctl clock draw --help
mini2ctl clock face NAME [seconds]
mini2ctl clock segments P1 P2 P3 P4 [seconds]
mini2ctl clock chars
mini2ctl brightness clock auto|0..80
mini2ctl brightness ring auto|0..100  # percent
mini2ctl light get
mini2ctl light enable on|off
mini2ctl light gain 1|3|6|9|18
mini2ctl light integration 0.025|0.05|0.1|0.2|0.4
mini2ctl ring set R G B [seconds]
mini2ctl ring zone 1..6 R G B [seconds]
mini2ctl ring clear
mini2ctl ring animate pulse|breathe|chase seconds
mini2ctl config check|reload|status
mini2ctl actions list
mini2ctl action run NAME
mini2ctl volume get|up|down|set 0..100
mini2ctl mute status|mute|unmute|toggle
mini2ctl buttons on|off              # default actions; privacy unchanged
mini2ctl events                      # JSON lines, Ctrl-C to stop
`

func clientMain() {
	args := os.Args[1:]
	args, handled, drawErr := drawingCLI(args)
	if drawErr != nil {
		fmt.Fprintln(os.Stderr, drawErr)
		os.Exit(1)
	}
	if handled {
		return
	}
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" {
		fmt.Print(help)
		return
	}
	c, e := net.DialTimeout("unix", socketPath, 2*time.Second)
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
	defer c.Close()
	json.NewEncoder(c).Encode(map[string]any{"args": args})
	scan := bufio.NewScanner(c)
	scan.Buffer(make([]byte, 1024), 262144)
	stream := args[0] == "events"
	if !stream {
		c.SetReadDeadline(time.Now().Add(15 * time.Second))
	}
	for scan.Scan() {
		if stream {
			fmt.Println(scan.Text())
			continue
		}
		var r map[string]any
		if json.Unmarshal(scan.Bytes(), &r) != nil {
			fmt.Println(scan.Text())
			continue
		}
		b, _ := json.MarshalIndent(r, "", "  ")
		fmt.Println(string(b))
		if r["ok"] == false {
			os.Exit(1)
		}
	}
	if e := scan.Err(); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
func pollTelemetry() {
	lastVol := ""
	for {
		if read(dir+"default") == "local" {
			out, e := cmd("amixer", "sget", "Master")
			v := ""
			for _, word := range strings.Fields(out) {
				if strings.HasSuffix(word, "%]") {
					v = strings.Trim(word, "[]%")
					break
				}
			}
			stateMu.Lock()
			sensorValue = read("/sys/bus/iio/devices/iio:device0/in_intensity_input")
			currentVolume, _ = strconv.Atoi(v)
			if e != nil {
				telemetryError = e.Error()
			} else {
				telemetryError = ""
			}
			stateMu.Unlock()
			if lastVol != "" && v != "" && v != lastVol {
				clockMu.Lock()
				volumeLast = v
				volumeUntil = time.Now().Add(2 * time.Second)
				clockMu.Unlock()
			}
			lastVol = v
		}
		time.Sleep(500 * time.Millisecond)
	}
}
func pollPairing() {
	for {
		out, _ := cmd("bluetoothctl", "show")
		stateMu.Lock()
		pairing = strings.Contains(out, "Discoverable: yes")
		stateMu.Unlock()
		time.Sleep(2 * time.Second)
	}
}

// GPIO sysfs exposes POLLPRI for edges; fall back to 20 ms sampling if unavailable.
func watchMuteButton(events chan<- buttonEvent) {
	path := "/sys/class/gpio/gpio419/"
	edgeErr := os.WriteFile(path+"edge", []byte("both"), 0644)
	f, e := os.Open(path + "value")
	if e != nil {
		return
	}
	defer f.Close()
	value := func() string {
		f.Seek(0, 0)
		b := make([]byte, 8)
		n, _ := f.Read(b)
		return strings.TrimSpace(string(b[:n]))
	}
	initial := value()
	debounce := debouncedSwitch{stable: initial, candidate: initial, since: time.Now()}
	for {
		if edgeErr == nil {
			pfd := struct {
				FD      int32
				Events  int16
				Revents int16
			}{int32(f.Fd()), 2 | 8, 0}
			_, _, errno := syscall.Syscall(syscall.SYS_POLL, uintptr(unsafe.Pointer(&pfd)), 1, 20)
			if errno != 0 {
				edgeErr = errno
			}
		}
		if edgeErr != nil {
			time.Sleep(20 * time.Millisecond)
		}
		current := value()
		if edgeErr != nil {
			current = read(path + "value")
		}
		if current == "" {
			edgeErr = errors.New("GPIO unavailable")
			time.Sleep(20 * time.Millisecond)
			continue
		}
		if stable, changed := debounce.sample(current, time.Now()); changed {
			v := 0
			if stable == "1" {
				v = 1
			}
			events <- buttonEvent{"mute", v}
		}
	}
}
