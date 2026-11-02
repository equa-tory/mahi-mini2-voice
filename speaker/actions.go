package main

import (
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"gopkg.in/yaml.v3"
)

type Frame struct {
	Segments [4]string `yaml:"segments"`
	MS       int       `yaml:"ms"`
}
type Action struct {
	DisplaySeconds *int     `yaml:"display_seconds,omitempty"`
	Type           string   `yaml:"type"`
	Args           []string `yaml:"args"`
	Steps          []Action `yaml:"steps"`
}
type BindingRule struct {
	When    string `yaml:"when" json:"when"`
	Action  string `yaml:"action" json:"action"`
	Consume bool   `yaml:"consume" json:"consume"`
}
type Configuration struct {
	VoiceStandbyDisabled bool                     `yaml:"voice_standby_disabled,omitempty"`
	RulesVersion         int                      `yaml:"action_rules_version,omitempty"`
	DisplaySeconds       int                      `yaml:"display_seconds,omitempty"`
	Conditional          map[string][]BindingRule `yaml:"conditional_bindings"`
	RepeatDelay          int                      `yaml:"repeat_delay_ms"`
	RepeatInterval       int                      `yaml:"repeat_interval_ms"`
	Active               [3]int                   `yaml:"active_rgb"`
	Version              int                      `yaml:"version"`
	Idle                 [3]int                   `yaml:"idle_rgb"`
	Bindings             map[string]string        `yaml:"bindings"`
	Actions              map[string]Action        `yaml:"actions"`
	Faces                map[string][]Frame       `yaml:"faces"`
}

var configMu sync.RWMutex
var activeConfig = defaults()
var configError string
var revision uint64
var runningActions = map[string]string{}

type invocation struct {
	ID     uint64
	Name   string
	Action Action
}

var actionQueue = make(chan invocation, 16)
var remoteActionQueue = make(chan invocation, 16)
var invocationID atomic.Uint64
var customFace []Frame
var faceStart time.Time

const configPath = dir + "controls.yaml"

func defaults() Configuration {
	return Configuration{RulesVersion: 2, DisplaySeconds: 3, Conditional: defaultConditions(), Version: 1, Bindings: map[string]string{"center.tap": "playback", "center.triple_tap": "timer.pick", "center.hold": "pair", "plus.press": "up", "plus.repeat": "up", "minus.press": "down", "minus.repeat": "down"}, Actions: map[string]Action{
		"timer.pick":     {Type: "timer", Args: []string{"pick"}},
		"timer.increase": {Type: "timer", Args: []string{"increase"}},
		"timer.decrease": {Type: "timer", Args: []string{"decrease"}},
		"timer.close":    {Type: "timer", Args: []string{"close"}},
		"timer.cancel":   {Type: "timer", Args: []string{"cancel"}},
		"assistant.stop": {Type: "assistant.stop"},
		"cancel":         {Type: "cancel.ordered"},
		"local.cancel":   {Type: "local.cancel"},
		"playback":       {Type: "playback.toggle"}, "pair": {Type: "bluetooth.pair"}, "up": {Type: "volume", Args: []string{"up"}}, "down": {Type: "volume", Args: []string{"down"}},
	}, Faces: map[string][]Frame{}}
}
func defaultConditions() map[string][]BindingRule {
	return map[string][]BindingRule{
		"center.tap":   {{When: "timer.active", Action: "timer.cancel", Consume: true}, {When: "timer.selecting", Action: "timer.close", Consume: true}, {When: "assistant.active", Action: "assistant.stop", Consume: true}, {When: "local.active", Action: "local.cancel", Consume: true}},
		"plus.press":   {{When: "timer.selecting", Action: "timer.increase", Consume: true}},
		"plus.repeat":  {{When: "timer.selecting", Action: "timer.increase", Consume: true}},
		"minus.press":  {{When: "timer.selecting", Action: "timer.decrease", Consume: true}},
		"minus.repeat": {{When: "timer.selecting", Action: "timer.decrease", Consume: true}},
	}
}
func validSegments(p [4]string) error {
	for _, s := range p {
		for _, c := range s {
			if !strings.ContainsRune("ABCDEFG", c) {
				return fmt.Errorf("invalid segment %q", c)
			}
		}
	}
	return nil
}
func validateAction(a Action, depth int) (int, error) {
	if a.DisplaySeconds != nil && (*a.DisplaySeconds < 1 || *a.DisplaySeconds > 86400) {
		return 0, errors.New("invalid display duration")
	}
	if depth > 4 {
		return 0, errors.New("sequence nesting exceeds 4")
	}
	switch a.Type {
	case "sequence":
		if len(a.Steps) == 0 || len(a.Steps) > 32 {
			return 0, errors.New("sequence needs 1..32 steps")
		}
		total := 0
		for _, s := range a.Steps {
			n, e := validateAction(s, depth+1)
			if e != nil {
				return 0, e
			}
			total += n
		}
		if total > 30000 {
			return 0, errors.New("sequence exceeds 30 seconds")
		}
		return total, nil
	case "delay":
		if len(a.Args) != 1 {
			return 0, errors.New("delay needs milliseconds")
		}
		n, e := strconv.Atoi(a.Args[0])
		if e != nil || n < 0 || n > 30000 {
			return 0, errors.New("invalid delay")
		}
		return n, nil
	case "volume", "mute", "clock", "ring", "timer", "weather":
		if e := validateCommand(a); e != nil {
			return 0, e
		}
		if a.Type == "mute" {
			return 12000, nil
		}
	case "playback.toggle", "bluetooth.pair", "assistant.stop", "local.cancel", "cancel.ordered":
		if len(a.Args) != 0 {
			return 0, errors.New("action takes no arguments")
		}
	case "server.request":
		if len(a.Args) == 0 {
			return 0, errors.New("server request needs a name")
		}
	default:
		return 0, fmt.Errorf("unknown action type %s", a.Type)
	}
	if a.Type == "server.request" || (a.Type == "weather" && len(a.Args) > 0 && a.Args[0] == "refresh") {
		return 10000, nil
	}
	if a.Type == "clock" || a.Type == "ring" || a.Type == "timer" {
		return 0, nil
	}
	if a.Type == "playback.toggle" {
		return 15000, nil
	}
	return 3000, nil
}
func loadConfiguration() (Configuration, error) {
	f, e := os.Open(configPath)
	if e != nil {
		return Configuration{}, e
	}
	defer f.Close()
	return decodeConfiguration(f)
}
func decodeConfiguration(reader io.Reader) (Configuration, error) {
	var e error
	var c Configuration
	d := yaml.NewDecoder(reader)
	d.KnownFields(true)
	if e = d.Decode(&c); e != nil {
		return c, e
	}
	if c.DisplaySeconds == 0 {
		c.DisplaySeconds = 3
	}
	if c.DisplaySeconds < 1 || c.DisplaySeconds > 86400 {
		return c, errors.New("invalid default display duration")
	}
	if c.Version != 1 {
		return c, errors.New("unsupported configuration version")
	}
	for _, n := range append(c.Idle[:], c.Active[:]...) {
		if n < 0 || n > 255 {
			return c, errors.New("invalid idle RGB")
		}
	}
	// Upgrade version-1 simple bindings without replacing any configured action.
	if c.Actions == nil {
		c.Actions = map[string]Action{}
	}
	if c.Bindings == nil {
		c.Bindings = map[string]string{}
	}
	for name, action := range defaults().Actions {
		if strings.HasPrefix(name, "timer.") || strings.HasPrefix(name, "assistant.") || name == "local.cancel" || name == "cancel" {
			if _, ok := c.Actions[name]; !ok {
				c.Actions[name] = action
			}
		}
	}
	if c.Conditional == nil {
		c.Conditional = defaultConditions()
		if _, ok := c.Bindings["center.triple_tap"]; !ok {
			c.Bindings["center.triple_tap"] = "timer.pick"
		}
	}
	if c.RulesVersion < 2 {
		if rules, ok := c.Conditional["center.tap"]; ok {
			found := false
			for _, rule := range rules {
				if rule.When == "local.active" {
					found = true
				}
			}
			if !found {
				c.Conditional["center.tap"] = append(rules, BindingRule{When: "local.active", Action: "local.cancel", Consume: true})
			}
		}
		c.RulesVersion = 2
	}
	for name, frames := range c.Faces {
		if len(frames) == 0 || len(frames) > 64 {
			return c, fmt.Errorf("invalid face %s", name)
		}
		for _, f := range frames {
			if e = validSegments(f.Segments); e != nil {
				return c, e
			}
			if len(frames) > 1 && f.MS < 100 {
				return c, errors.New("animation frames require >=100ms")
			}
		}
	}
	for name, a := range c.Actions {
		if e := validateFaces(a, c.Faces); e != nil {
			return c, e
		}
		if _, e = validateAction(a, 0); e != nil {
			return c, fmt.Errorf("%s: %w", name, e)
		}
	}
	if c.RepeatDelay != 0 && (c.RepeatDelay < 100 || c.RepeatDelay > 5000) {
		return c, errors.New("repeat delay must be 100..5000ms")
	}
	if c.RepeatInterval != 0 && (c.RepeatInterval < 50 || c.RepeatInterval > 2000) {
		return c, errors.New("repeat interval must be 50..2000ms")
	}
	for key, rules := range c.Conditional {
		if len(rules) > 32 {
			return c, errors.New("too many conditional rules")
		}
		for _, rule := range rules {
			if rule.When != "always" && rule.When != "assistant.active" && rule.When != "timer.active" && rule.When != "timer.selecting" && rule.When != "local.active" {
				return c, errors.New("unknown binding condition")
			}
			if _, ok := c.Actions[rule.Action]; !ok {
				return c, errors.New("unknown conditional action")
			}
		}
		parts := strings.Split(key, ".")
		if len(parts) != 2 || !strings.Contains("|center|plus|minus|mute|", "|"+parts[0]+"|") || !strings.Contains("|press|release|repeat|tap|double_tap|triple_tap|hold|", "|"+parts[1]+"|") || key == "mute.hold" {
			return c, errors.New("invalid conditional gesture")
		}
	}
	for key, name := range c.Bindings {
		parts := strings.Split(key, ".")
		if len(parts) != 2 {
			return c, fmt.Errorf("invalid binding %s", key)
		}
		if !strings.Contains("|center|plus|minus|mute|", "|"+parts[0]+"|") {
			return c, errors.New("unknown button")
		}
		if !strings.Contains("|press|release|repeat|tap|double_tap|triple_tap|hold|", "|"+parts[1]+"|") {
			return c, errors.New("unknown gesture")
		}
		if parts[0] == "mute" && parts[1] == "hold" {
			return c, errors.New("mute hold unsupported")
		}
		if _, ok := c.Actions[name]; !ok {
			return c, fmt.Errorf("unknown action %s", name)
		}
	}
	return c, nil
}
func initActions() {
	c, e := loadConfiguration()
	configMu.Lock()
	if e == nil {
		activeConfig = c
	} else if !os.IsNotExist(e) {
		configError = e.Error()
	}
	revision++
	configMu.Unlock()
	go runActionWorker(actionQueue, "local")
	go runActionWorker(remoteActionQueue, "remote")
}
func runActionWorker(queue <-chan invocation, lane string) {
	for job := range queue {
		configMu.Lock()
		runningActions[lane] = job.Name
		configMu.Unlock()
		actionEvent(job, "action_started", "")
		e := executeInvocation(job)
		msg := ""
		kind := "action_completed"
		if e != nil {
			msg = e.Error()
			kind = "action_failed"
			overlay("Err")
		}
		actionEvent(job, kind, msg)
		configMu.Lock()
		delete(runningActions, lane)
		configMu.Unlock()
	}
}
func needsServer(a Action) bool {
	if a.Type == "server.request" || (a.Type == "weather" && len(a.Args) > 0 && a.Args[0] == "refresh") {
		return true
	}
	if a.Type == "assistant.stop" {
		return true
	}
	for _, step := range a.Steps {
		if needsServer(step) {
			return true
		}
	}
	return false
}

// Voice and button cancellation share the configured center-tap priority rules.
func nextCancellation() string {
	configMu.RLock()
	rules := append([]BindingRule(nil), activeConfig.Conditional["center.tap"]...)
	configMu.RUnlock()
	for _, rule := range rules {
		matched := false
		switch rule.Action {
		case "timer.cancel":
			matched = speakerTimer.snapshot(time.Now())["active"] == true
		case "timer.close":
			matched = pickerActive()
		case "assistant.stop":
			matched = assistantActive()
		case "local.cancel":
			matched = localActionActive()
		}
		if matched {
			return rule.Action
		}
	}
	return ""
}

func cancelOrdered() (string, error) {
	name := nextCancellation()
	if name == "" {
		return "", nil
	}
	configMu.RLock()
	action := activeConfig.Actions[name]
	configMu.RUnlock()
	return name, executeAction(action)
}

func idleColor() [3]int { configMu.RLock(); defer configMu.RUnlock(); return activeConfig.Idle }
func triggerBinding(button, kind string, count int) {
	if read(dir+"default") != "local" || read(dir+"button-actions") == "0" {
		return
	}
	if kind == "multi_tap" {
		if count == 2 {
			kind = "double_tap"
		} else if count == 3 {
			kind = "triple_tap"
		} else {
			return
		}
	}
	configMu.RLock()
	name := activeConfig.Bindings[button+"."+kind]
	rules := activeConfig.Conditional[button+"."+kind]
	configMu.RUnlock()
	for _, rule := range rules {
		matched := (rule.When == "local.active" && localActionActive()) || rule.When == "always" || (rule.When == "assistant.active" && assistantActive()) || (rule.When == "timer.active" && speakerTimer.snapshot(time.Now())["active"] == true) || (rule.When == "timer.selecting" && pickerActive())
		if matched {
			configMu.RLock()
			action := activeConfig.Actions[rule.Action]
			configMu.RUnlock()
			if action.Type == "timer" || action.Type == "local.cancel" || action.Type == "assistant.stop" {
				if e := executeAction(action); e != nil {
					overlay("Err")
				}
			} else if _, e := enqueue(rule.Action); e != nil {
				overlay("Err")
			}
			if rule.Consume {
				return
			}
		}
	}
	if name != "" {
		if _, e := enqueue(name); e != nil {
			overlay("Err")
		}
	}
}
func enqueue(name string) (uint64, error) {
	configMu.RLock()
	a, ok := activeConfig.Actions[name]
	configMu.RUnlock()
	if !ok {
		return 0, errors.New("unknown action")
	}
	j := invocation{invocationID.Add(1), name, a}
	if a.Type == "cancel.ordered" {
		actionEvent(j, "action_started", "")
		err := executeAction(a)
		if err != nil {
			actionEvent(j, "action_failed", err.Error())
		} else {
			actionEvent(j, "action_completed", "")
		}
		return j.ID, err
	}
	queue := actionQueue
	if needsServer(a) {
		queue = remoteActionQueue
	}
	select {
	case queue <- j:
		return j.ID, nil
	default:
		actionEvent(j, "action_failed", "queue full")
		return 0, errors.New("action queue full")
	}
}
func actionEvent(j invocation, kind, msg string) {
	emitFields("", kind, 0, 0, map[string]any{"invocation_id": j.ID, "action": j.Name, "error": msg})
}
func executeAction(a Action) error {
	switch a.Type {
	case "sequence":
		for _, s := range a.Steps {
			if e := executeAction(s); e != nil {
				return e
			}
		}
		return nil
	case "delay":
		n, _ := strconv.Atoi(a.Args[0])
		time.Sleep(time.Duration(n) * time.Millisecond)
		return nil
	case "playback.toggle":
		s := toggle()
		if s == "IDLE" {
			startMotion("pulse", 2)
			return nil
		}
		overlay(s)
		if s == "Err" {
			return errors.New("playback failed")
		}
		return nil
	case "bluetooth.pair":
		_, e := cmd("/data/opt/bin/mini2-mode", "pair")
		if e == nil {
			stateMu.Lock()
			pairing = true
			stateMu.Unlock()
		}
		return e
	case "local.cancel":
		cancelLocalAction(0)
		return nil
	case "cancel.ordered":
		_, err := cancelOrdered()
		return err
	case "assistant.stop":
		return stopAssistant()
	case "server.request":
		emitFields("", "server_request", 0, 0, map[string]any{"request": a.Args[0], "args": a.Args[1:]})
		return requestServer(a.Args)
	default:
		_, e := dispatch(append([]string{a.Type}, a.Args...))
		return e
	}
}
func displayPatterns(now time.Time, text string) (p [4]string) {
	if customFace != nil && !timerIsRinging() && (userUntil.IsZero() || now.Before(userUntil)) && !now.Before(volumeUntil) && !now.Before(actionUntil) {
		total := 0
		for _, f := range customFace {
			total += f.MS
		}
		idx := 0
		if total > 0 {
			n := int(now.Sub(faceStart).Milliseconds() % int64(total))
			for i, f := range customFace {
				if n < f.MS {
					idx = i
					break
				}
				n -= f.MS
			}
		}
		return customFace[idx].Segments
	}
	for i, c := range strings.ToUpper(text) {
		if i < 4 {
			p[i] = font[c]
		}
	}
	return
}
func extension(args []string) (any, error, bool) {
	if v, e, ok := voiceLeaseCommand(args); ok {
		return v, e, ok
	}
	if v, e, ok := weatherCommand(args); ok {
		return v, e, ok
	}
	if v, e, ok := timerCommand(args); ok {
		return v, e, ok
	}
	if v, e, ok := drawingCommand(args); ok {
		return v, e, ok
	}
	if v, e, ok := motionCommand(args); ok {
		return v, e, ok
	}
	if len(args) == 0 {
		return nil, nil, false
	}
	switch args[0] {
	case "config":
		if len(args) == 2 && args[1] == "effective" {
			configMu.RLock()
			b, e := yaml.Marshal(activeConfig)
			configMu.RUnlock()
			return string(b), e, true
		}
		if len(args) == 2 && args[1] == "get" {
			b, e := os.ReadFile(configPath)
			return string(b), e, true
		}
		if len(args) == 3 && args[1] == "apply" {
			if read(dir+"default") != "local" {
				return nil, errors.New("local mode required"), true
			}
			if len(args[2]) > 65536 {
				return nil, errors.New("configuration exceeds64KiB"), true
			}
			c, e := decodeConfiguration(strings.NewReader(args[2]))
			if e != nil {
				return nil, e, true
			}
			configMu.Lock()
			defer configMu.Unlock()
			if e = atomicWrite(configPath, args[2]); e != nil {
				return nil, e, true
			}
			activeConfig = c
			revision++
			configError = ""
			return "configuration applied", nil, true
		}
		if len(args) != 2 {
			return nil, errors.New("config check|reload|status"), true
		}
		if args[1] == "status" {
			configMu.RLock()
			defer configMu.RUnlock()
			return map[string]any{"conditional_bindings": activeConfig.Conditional, "bindings": activeConfig.Bindings, "repeat_delay_ms": activeConfig.RepeatDelay, "repeat_interval_ms": activeConfig.RepeatInterval, "revision": revision, "error": configError}, nil, true
		}
		if args[1] != "check" && args[1] != "reload" {
			return nil, errors.New("config check|reload|status"), true
		}
		c, e := loadConfiguration()
		if e != nil {
			return nil, e, true
		}
		if args[1] == "reload" {
			configMu.Lock()
			activeConfig = c
			revision++
			configError = ""
			configMu.Unlock()
		}
		return "configuration valid", nil, true
	case "actions":
		if len(args) != 2 || args[1] != "list" {
			return nil, errors.New("actions list"), true
		}
		configMu.RLock()
		defer configMu.RUnlock()
		return activeConfig.Actions, nil, true
	case "action":
		if len(args) == 2 && args[1] == "stop" {
			value, err := cancelOrdered()
			return value, err, true
		}
		if (len(args) == 2 || len(args) == 3) && args[1] == "cancel" {
			var id uint64
			if len(args) == 3 {
				var e error
				id, e = strconv.ParseUint(args[2], 10, 64)
				if e != nil {
					return nil, e, true
				}
			}
			return cancelLocalAction(id), nil, true
		}
		if read(dir+"default") != "local" {
			return nil, errors.New("local mode required"), true
		}
		if len(args) == 3 && args[1] == "run" {
			id, e := enqueue(args[2])
			return id, e, true
		}
		return nil, errors.New("action run NAME"), true
	case "clock":
		if len(args) < 3 || (args[1] != "segments" && args[1] != "face") {
			return nil, nil, false
		}
		var frames []Frame
		seconds := displaySeconds()
		var e error
		if args[1] == "segments" {
			if len(args) != 6 && len(args) != 7 {
				return nil, errors.New("clock segments P1 P2 P3 P4 [seconds]"), true
			}
			f := Frame{Segments: [4]string{args[2], args[3], args[4], args[5]}}
			if e = validSegments(f.Segments); e != nil {
				return nil, e, true
			}
			frames = []Frame{f}
			if len(args) == 7 {
				seconds, e = integer(args[6], 0, 86400)
			}
		} else {
			if len(args) > 4 {
				return nil, errors.New("clock face NAME [seconds]"), true
			}
			configMu.RLock()
			frames = activeConfig.Faces[args[2]]
			configMu.RUnlock()
			if len(frames) == 0 {
				return nil, errors.New("unknown face"), true
			}
			if len(args) == 4 {
				seconds, e = integer(args[3], 0, 86400)
			}
		}
		if e != nil {
			return nil, e, true
		}
		if read(dir+"default") != "local" {
			return nil, errors.New("local mode required"), true
		}
		clockMu.Lock()
		customFace = frames
		faceStart = time.Now()
		userText = ""
		userUntil = time.Time{}
		if seconds > 0 {
			userUntil = faceStart.Add(time.Duration(seconds) * time.Second)
		}
		clockLast = ""
		clockMu.Unlock()
		registerDisplay("face", seconds)
		return "face set", nil, true
	}
	return nil, nil, false
}

func validateCommand(a Action) error {
	x := a.Args
	bad := errors.New("invalid " + a.Type + " action arguments")
	if len(x) == 0 {
		return bad
	}
	switch a.Type {
	case "weather":
		if len(x) == 1 && (x[0] == "show" || x[0] == "status" || x[0] == "refresh") {
			return nil
		}
		if len(x) == 2 && x[0] == "show" {
			_, e := integer(x[1], 1, 86400)
			return e
		}
	case "timer":
		if len(x) == 2 && x[0] == "sound-level" {
			_, e := integer(x[1], 0, 100)
			return e
		}
		if len(x) == 1 && x[0] == "sound-test" {
			return nil
		}
		if len(x) == 2 && x[0] == "start" {
			_, e := parseTimerDuration(x[1])
			return e
		}
		if len(x) == 1 && (x[0] == "pick" || x[0] == "increase" || x[0] == "decrease" || x[0] == "close" || x[0] == "status" || x[0] == "cancel" || x[0] == "dismiss") {
			return nil
		}
	case "volume":
		if len(x) == 1 && (x[0] == "up" || x[0] == "down" || x[0] == "get") {
			return nil
		}
		if len(x) == 2 && x[0] == "set" {
			_, e := integer(x[1], 0, 100)
			return e
		}
	case "mute":
		if len(x) == 1 && (x[0] == "mute" || x[0] == "unmute" || x[0] == "toggle" || x[0] == "status") {
			return nil
		}
	case "clock":
		if (len(x) == 2 || len(x) == 3) && x[0] == "draw" {
			if _, e := parseDrawing(x[1]); e != nil {
				return e
			}
			if len(x) == 3 {
				_, e := integer(x[2], 0, 86400)
				return e
			}
			return nil
		}
		if len(x) == 1 && x[0] == "clear" {
			return nil
		}
		if (len(x) == 2 || len(x) == 3) && x[0] == "text" {
			if e := validateText(x[1]); e != nil {
				return e
			}
			if len(x) == 3 {
				_, e := integer(x[2], 0, 86400)
				return e
			}
			return nil
		}
		if (len(x) == 2 || len(x) == 3) && x[0] == "face" {
			if len(x) == 3 {
				_, e := integer(x[2], 0, 86400)
				return e
			}
			return nil
		}
	case "ring":
		if len(x) == 3 && x[0] == "animate" {
			_, e, ok := validateMotionArgs(x)
			if ok {
				return e
			}
		}
		if len(x) == 1 && x[0] == "clear" {
			return nil
		}
		if (len(x) == 4 || len(x) == 5) && x[0] == "set" {
			for _, n := range x[1:4] {
				if _, e := integer(n, 0, 255); e != nil {
					return e
				}
			}
			if len(x) == 5 {
				_, e := integer(x[4], 0, 86400)
				return e
			}
			return nil
		}
	}
	return bad
}

func validateFaces(a Action, faces map[string][]Frame) error {
	if a.Type == "clock" && len(a.Args) > 1 && a.Args[0] == "face" && len(faces[a.Args[1]]) == 0 {
		return fmt.Errorf("unknown face %s", a.Args[1])
	}
	for _, s := range a.Steps {
		if e := validateFaces(s, faces); e != nil {
			return e
		}
	}
	return nil
}
func configurationStatus() map[string]any {
	configMu.RLock()
	defer configMu.RUnlock()
	return map[string]any{"conditional_bindings": activeConfig.Conditional, "bindings": activeConfig.Bindings, "repeat_delay_ms": activeConfig.RepeatDelay, "repeat_interval_ms": activeConfig.RepeatInterval, "revision": revision, "error": configError, "active_actions": copyRunningActions(), "queue_depth": len(actionQueue), "remote_queue_depth": len(remoteActionQueue), "server_configured": read(dir+"server.json") != ""}
}

func validateMotionArgs(x []string) (int, error, bool) {
	if len(x) != 3 || x[0] != "animate" {
		return 0, nil, false
	}
	if x[1] != "pulse" && x[1] != "breathe" && x[1] != "chase" {
		return 0, errors.New("unknown animation"), true
	}
	n, e := integer(x[2], 1, 86400)
	return n, e, true
}

func copyRunningActions() map[string]string {
	m := map[string]string{}
	for k, v := range runningActions {
		m[k] = v
	}
	return m
}
