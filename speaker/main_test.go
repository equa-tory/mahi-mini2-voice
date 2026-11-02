package main

import (
	"testing"
	"time"
)

func TestSourceSelection(t *testing.T) {
	cases := []struct {
		name         string
		air, playing bool
		bt           int
		btPlaying    bool
		last, want   string
	}{
		{"airplay2_only", true, true, 0, false, "", "air"},
		{"playing_air_not_paused_pc", true, true, 1, false, "", "air"},
		{"playing_pc_not_paused_air", true, false, 1, true, "", "bt"},
		{"both_playing_ambiguous", true, true, 1, true, "", ""},
		{"resume_last_air", true, false, 1, false, "air", "air"},
		{"resume_last_pc", true, false, 1, false, "bt", "bt"},
		{"both_idle_no_owner", true, false, 1, false, "", ""},
		{"no_session", false, false, 0, false, "", ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := pickSource(c.air, c.playing, c.bt, c.btPlaying, c.last); got != c.want {
				t.Fatalf("got %q want %q", got, c.want)
			}
		})
	}
}

func TestClockAlphabet(t *testing.T) {
	for _, s := range []string{"HELO", "12-3", "?![]", "a_b="} {
		if e := validateText(s); e != nil {
			t.Errorf("%q: %v", s, e)
		}
	}
	for _, s := range []string{"HELLO", "M", "W", "Ж", "."} {
		if validateText(s) == nil {
			t.Errorf("accepted unsupported %q", s)
		}
	}
}
func TestGestures(t *testing.T) {
	start := time.Unix(100, 0)
	for _, button := range []string{"center", "mute", "plus", "minus"} {
		g := gesture{}
		got := ""
		count := 0
		action := func(k string, n int) { got = k; count = n }
		g.input(button, 1, start)
		g.input(button, 0, start.Add(80*time.Millisecond))
		g.tick(button, start.Add(450*time.Millisecond), action)
		if got != "tap" || count != 1 {
			t.Fatalf("%s single: %s %d", button, got, count)
		}
		g = gesture{}
		got = ""
		g.input(button, 1, start)
		g.input(button, 0, start.Add(50*time.Millisecond))
		g.input(button, 1, start.Add(150*time.Millisecond))
		g.input(button, 0, start.Add(200*time.Millisecond))
		g.tick(button, start.Add(600*time.Millisecond), action)
		if got != "multi_tap" || count != 2 {
			t.Fatalf("%s double: %s %d", button, got, count)
		}
		g = gesture{}
		got = ""
		g.input(button, 1, start)
		g.tick(button, start.Add(3*time.Second), action)
		if got != "hold" {
			t.Fatalf("%s hold: %s", button, got)
		}
		got = ""
		g.input(button, 0, start.Add(4*time.Second))
		g.tick(button, start.Add(5*time.Second), action)
		if got != "" {
			t.Fatalf("%s fired tap after hold", button)
		}
	}
}

func TestSoftwareRepeatAndTapGrouping(t *testing.T) {
	start := time.Now()
	g := gesture{}
	g.input("plus", 1, start)
	first := g.nextRepeat
	if first.Sub(start) != 500*time.Millisecond {
		t.Fatal(first.Sub(start))
	}
	g.input("plus", 2, start.Add(100*time.Millisecond))
	if g.nextRepeat != first {
		t.Fatal("hardware repeat changed schedule")
	}
	g.tick("plus", start.Add(499*time.Millisecond), func(string, int) {})
	if g.nextRepeat != first {
		t.Fatal("early repeat")
	}
	g.tick("plus", first, func(string, int) {})
	if g.nextRepeat.Sub(first) != 150*time.Millisecond {
		t.Fatal("repeat interval")
	}
	g.input("plus", 0, first.Add(time.Millisecond))
	next := g.nextRepeat
	g.tick("plus", first.Add(time.Second), func(string, int) {})
	if g.nextRepeat != next {
		t.Fatal("repeat after release")
	}
	g = gesture{}
	calls := []string{}
	for i := 0; i < 3; i++ {
		at := start.Add(time.Duration(i) * 150 * time.Millisecond)
		g.input("center", 1, at)
		g.input("center", 0, at.Add(50*time.Millisecond))
		g.tick("center", at.Add(100*time.Millisecond), func(kind string, _ int) { calls = append(calls, kind) })
	}
	g.tick("center", start.Add(time.Second), func(kind string, n int) {
		if n != 3 {
			t.Fatal(n)
		}
		calls = append(calls, kind)
	})
	if len(calls) != 1 || calls[0] != "multi_tap" {
		t.Fatal(calls)
	}
}
