package main

import (
	"gopkg.in/yaml.v3"
	"os"
	"strings"
	"testing"
	"time"
)

func TestActionValidation(t *testing.T) {
	for _, a := range []Action{{Type: "shell"}, {Type: "volume", Args: []string{"set", "101"}}, {Type: "sequence", Steps: []Action{{Type: "delay", Args: []string{"30000"}}, {Type: "delay", Args: []string{"1"}}}}, {Type: "mute", Args: []string{"restore"}}} {
		if _, e := validateAction(a, 0); e == nil {
			t.Fatalf("accepted %#v", a)
		}
	}
	if _, e := validateAction(Action{Type: "sequence", Steps: []Action{{Type: "volume", Args: []string{"down"}}, {Type: "delay", Args: []string{"100"}}}}, 0); e != nil {
		t.Fatal(e)
	}
}
func TestSegmentFrames(t *testing.T) {
	clockMu.Lock()
	defer clockMu.Unlock()
	customFace = []Frame{{Segments: [4]string{"A", "B", "C", "D"}, MS: 100}, {Segments: [4]string{"G", "", "", "G"}, MS: 100}}
	defer func() { customFace = nil }()
	faceStart = time.Now()
	userUntil = time.Time{}
	actionUntil = time.Time{}
	volumeUntil = time.Time{}
	if p := displayPatterns(faceStart.Add(150*time.Millisecond), "1234"); p[0] != "G" {
		t.Fatal(p)
	}
	userUntil = faceStart.Add(100 * time.Millisecond)
	if p := displayPatterns(faceStart.Add(150*time.Millisecond), "1234"); p[0] != "BC" {
		t.Fatal(p)
	}
	if validSegments([4]string{"X"}) == nil {
		t.Fatal("accepted X")
	}
}
func TestIdleOff(t *testing.T) {
	f, _ := selectRing(ringView{Idle: [3]int{}, Scale: 100})
	if f != solid([3]int{}) {
		t.Fatal(f)
	}
}
func TestQueueBound(t *testing.T) {
	configMu.Lock()
	old := activeConfig
	activeConfig = defaults()
	configMu.Unlock()
	defer func() {
		configMu.Lock()
		activeConfig = old
		configMu.Unlock()
		for len(actionQueue) > 0 {
			<-actionQueue
		}
	}()
	for i := 0; i < 16; i++ {
		if _, e := enqueue("up"); e != nil {
			t.Fatal(e)
		}
	}
	if _, e := enqueue("up"); e == nil {
		t.Fatal("queue overflow accepted")
	}
}

func TestExampleActions(t *testing.T) {
	b, e := os.ReadFile("controls.example.yaml")
	if e != nil {
		t.Fatal(e)
	}
	var c Configuration
	if e = yaml.Unmarshal(b, &c); e != nil {
		t.Fatal(e)
	}
	for name, a := range c.Actions {
		if _, e = validateAction(a, 0); e != nil {
			t.Fatalf("%s: %v", name, e)
		}
	}
}

func TestConfigurationDecodeValidation(t *testing.T) {
	b, e := os.ReadFile("controls.example.yaml")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = decodeConfiguration(strings.NewReader(string(b))); e != nil {
		t.Fatal(e)
	}
	bad := "version: 1\nbindings: {mute.hold: up}\nactions: {up: {type: volume, args: [up]}}"
	if _, e = decodeConfiguration(strings.NewReader(bad)); e == nil {
		t.Fatal("invalid binding accepted")
	}
	if _, e = decodeConfiguration(strings.NewReader("version: 1\nunknown: true")); e == nil {
		t.Fatal("unknown setting accepted")
	}
}

func TestConditionalBindingsValidation(t *testing.T) {
	for _, body := range []string{
		"version: 1\nconditional_bindings:\n  center.tap: [{when: unknown, action: timer.cancel}]\n",
		"version: 1\nconditional_bindings:\n  center.bogus: [{when: always, action: timer.cancel}]\n",
		"version: 1\nrepeat_interval_ms: 1\n",
	} {
		if _, e := decodeConfiguration(strings.NewReader(body)); e == nil {
			t.Fatal(body)
		}
	}
	c, e := decodeConfiguration(strings.NewReader("version: 1\n"))
	if e != nil || c.Bindings["center.triple_tap"] != "timer.pick" || len(c.Conditional["center.tap"]) != 4 || c.Conditional["center.tap"][0].When != "timer.active" {
		t.Fatal(c, e)
	}
	c, e = decodeConfiguration(strings.NewReader("version: 1\nconditional_bindings: {}\nbindings: {plus.double_tap: timer.pick}\n"))
	if e != nil || len(c.Conditional) != 0 || c.Bindings["plus.double_tap"] != "timer.pick" {
		t.Fatal(c, e)
	}
}
