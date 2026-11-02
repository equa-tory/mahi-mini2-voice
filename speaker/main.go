package main

import (
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

var micMu sync.Mutex

const dir = "/data/opt/local-mode/"

func read(p string) string { b, _ := os.ReadFile(p); return strings.TrimSpace(string(b)) }
func write(p, s string)    { os.WriteFile(p, []byte(s), 0644) }
func cmd(args ...string) (string, error) {
	if len(args) > 0 && args[0] == "/data/opt/bin/mini2-mic" {
		micMu.Lock()
		defer micMu.Unlock()
	}
	limit := 3 * time.Second
	if len(args) > 0 && args[0] == "/data/opt/bin/mini2-mic" {
		limit = 12 * time.Second
	}
	ctx, c := context.WithTimeout(context.Background(), limit)
	defer c()
	b, e := exec.CommandContext(ctx, args[0], args[1:]...).CombinedOutput()
	return string(b), e
}
func db(private bool, dest, path, method string) (string, error) {
	args := []string{"gdbus", "call", "--system", "--dest", dest, "--object-path", path, "--method", method}
	ctx, c := context.WithTimeout(context.Background(), 3*time.Second)
	defer c()
	x := exec.CommandContext(ctx, args[0], args[1:]...)
	if private {
		x.Env = append(os.Environ(), "DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/station-airplay/private-bus")
	}
	b, e := x.CombinedOutput()
	return string(b), e
}

var lastPlayback string
var playbackMu sync.Mutex

func pickSource(air, playing bool, count int, btPlaying bool, last string) string {
	if air && count > 0 {
		if playing && btPlaying {
			return ""
		}
		if playing {
			return "air"
		}
		if btPlaying && count == 1 {
			return "bt"
		}
		if last == "air" {
			return "air"
		}
		if last == "bt" && count == 1 {
			return "bt"
		}
		return ""
	}
	if air {
		return "air"
	}
	if count == 1 {
		return "bt"
	}
	return ""
}

func toggle() string {
	playbackMu.Lock()
	defer playbackMu.Unlock()
	// Inspect both sources, including paused sessions, before choosing one.
	a, _ := cmd("sh", "-c", "DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/station-airplay/private-bus gdbus call --system --dest org.gnome.ShairportSync --object-path /org/gnome/ShairportSync --method org.freedesktop.DBus.Properties.GetAll org.gnome.ShairportSync.RemoteControl")
	modern, _ := db(true, "org.gnome.ShairportSync", "/org/gnome/ShairportSync", "org.gnome.ShairportSync.GetControlStatus")
	air := (strings.Contains(a, "'Available': <true>") || strings.Contains(modern, ", true,")) && strings.Contains(a, "'Client': <'") && !strings.Contains(a, "'Client': <''>")
	b, _ := cmd("gdbus", "call", "--system", "--dest", "org.bluez", "--object-path", "/", "--method", "org.freedesktop.DBus.ObjectManager.GetManagedObjects")
	btPath := ""
	btState := ""
	count := 0
	// BlueZ returns each object as a separate section.
	parts := strings.Split(b, "'/org/bluez/")
	for _, p := range parts {
		if !strings.Contains(p, "org.bluez.MediaPlayer1") {
			continue
		}
		end := strings.Index(p, "'")
		if end < 0 {
			continue
		}
		count++
		btPath = "/org/bluez/" + p[:end]
		if strings.Contains(p, "'Status': <'playing'>") {
			btState = "playing"
		}
	}
	airPlaying := strings.Contains(a, "'PlayerState': <'Playing'>")
	source := pickSource(air, airPlaying, count, btState == "playing", lastPlayback)
	if source == "" {
		if !air && count == 0 {
			return "IDLE"
		}
		return "Err"
	}
	air = source == "air"
	state := "PLAY"
	var e error
	if air {
		if strings.Contains(a, "'PlayerState': <'Playing'>") {
			state = "PAUS"
		}
		method := "Play"
		if airPlaying {
			method = "Pause"
		}
		_, e = db(true, "org.gnome.ShairportSync", "/org/gnome/ShairportSync", "org.gnome.ShairportSync.RemoteControl."+method)
		if e == nil {
			lastPlayback = "air"
		}
	} else {
		method := "Play"
		if btState == "playing" {
			method = "Pause"
			state = "PAUS"
		}
		_, e = db(false, "org.bluez", btPath, "org.bluez.MediaPlayer1."+method)
		if e == nil {
			lastPlayback = "bt"
		}
	}
	if e != nil {
		return "Err"
	}
	return state
}

type buttonEvent struct {
	button string
	value  int
}

func main() {
	if len(os.Args) > 1 || strings.HasSuffix(os.Args[0], "mini2ctl") {
		clientMain()
		return
	}
	lock, e := os.OpenFile("/tmp/mini2-controls.lock", os.O_CREATE|os.O_RDWR, 0600)
	if e != nil || syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		os.Stderr.WriteString("mini2-controls already running or cannot lock\n")
		os.Exit(1)
	}
	defer lock.Close()

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	for _, pin := range []string{"414", "419"} {
		if _, e := os.Stat("/sys/class/gpio/gpio" + pin); e != nil {
			write("/sys/class/gpio/export", pin)
		}
	}
	if hardwareMuted() {
		cmd("/data/opt/bin/mini2-mic", "--raw", "mute")
	} else {
		cmd("/data/opt/bin/mini2-mic", "--raw", "restore")
	}
	setupPrivacy()
	initActions()
	speakerTimer.restore(time.Now())
	previousHardwareMute := hardwareMuted()
	go serveControls()
	go pollTelemetry()
	go pollPairing()
	events := make(chan buttonEvent, 64)
	go watchMuteButton(events)
	go func() {
		f, e := os.Open("/dev/input/event0")
		if e != nil {
			return
		}
		defer f.Close()
		b := make([]byte, 16)
		for {
			if _, e = io.ReadFull(f, b); e != nil {
				events <- buttonEvent{"input_lost", 0}
				return
			}
			if binary.LittleEndian.Uint16(b[8:]) == 1 {
				name := map[uint16]string{582: "center", 114: "minus", 115: "plus"}[binary.LittleEndian.Uint16(b[10:])]
				if name != "" {
					events <- buttonEvent{name, int(binary.LittleEndian.Uint32(b[12:]))}
				}
			}
		}
	}()
	tick := time.NewTicker(50 * time.Millisecond)
	defer tick.Stop()
	gestures := map[string]*gesture{"center": {}, "mute": {}, "plus": {}, "minus": {}}
	action := func(button, kind string, count int) {
		if read(dir+"button-actions") != "0" {
			triggerBinding(button, kind, count)
		}
	}
	until := time.Time{}
	auto := 5
	ambient := 5
	lux := 0.0
	cycles := 0
	for {
		select {
		case <-signals:
			os.Remove(socketPath)
			if privacy.snapshot().Busy {
				privacy.await(privacy.snapshot().Revision)
			}
			return
		case v := <-events:
			if v.button == "input_lost" {
				for name, g := range gestures {
					*g = gesture{}
					stateMu.Lock()
					pressed[name] = false
					stateMu.Unlock()
				}
				continue
			}
			if read(dir+"default") == "local" {

				gestures[v.button].input(v.button, v.value, time.Now())
			}
		case now := <-tick.C:
			if read(dir+"default") != "local" {
				ringValid = false
				clockLast = ""
				for _, g := range gestures {
					*g = gesture{}
				}
				continue
			}
			physical := hardwareMuted()
			if physical != previousHardwareMute {
				previousHardwareMute = physical
				privacy.request(&physical)
			}
			for button, g := range gestures {
				name := button
				g.tick(button, now, func(kind string, count int) { action(name, kind, count) })
			}
			speakerTimer.tick(now)
			renderClock(now)
			cycles++
			if cycles%40 == 1 {
				value, e := strconv.ParseFloat(read("/sys/bus/iio/devices/iio:device0/in_intensity_input"), 64)
				if e == nil && value >= 0 {
					lux = 0.75*lux + 0.25*value
				}
				target := 2
				if lux >= 1 {
					target = 4
				}
				if lux >= 10 {
					target = 8
				}
				if lux >= 100 {
					target = 16
				}
				if ambient < target {
					ambient++
				}
				if ambient > target {
					ambient--
				}

				if read(dir+"auto-brightness") == "1" {
					auto = ambient
				} else {
					auto, _ = strconv.Atoi(read(dir + "clock-brightness"))
					if auto < 0 || auto > 80 {
						auto = 5
					}
				}
				write("/tmp/mini2-effective-brightness", strconv.Itoa(auto))
				write("/tmp/mini2-lux", fmt.Sprintf("%.3f", lux))
			}
			action := read("/tmp/mini2-last-action")
			if action != "" {
				os.Remove("/tmp/mini2-last-action")
				if action == "Err" {
					until = now.Add(time.Second)
				}
			}
			renderRing(ringViewNow(now, ambient, until), cycles%10 == 0)
		}
	}
}
