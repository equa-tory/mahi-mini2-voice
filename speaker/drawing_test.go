package main

import (
	"strings"
	"testing"
	"time"
)

func TestDrawingTemplate(t *testing.T) {
	p, e := parseDrawing(drawingTemplate)
	if e != nil {
		t.Fatal(e)
	}
	for _, s := range p {
		for _, seg := range "ABCDEFG" {
			if !strings.ContainsRune(s, seg) {
				t.Fatal(p)
			}
		}
	}
}
func TestDrawingBlankAndCRLF(t *testing.T) {
	p, e := parseDrawing("\r\n\r\n\r\n")
	if e != nil || p != [4]string{} {
		t.Fatal(p, e)
	}
	p, e = parseDrawing(" _\n|_|\n|_|\n")
	if e != nil || len(p[0]) != 7 || p[1] != "" {
		t.Fatal(p, e)
	}
}
func TestInvalidDrawings(t *testing.T) {
	for _, s := range []string{"one line", "\n\n\n\n", "XXXXXXXXXXXXXXXX\n\n", "    x\n\n", "   _\n\n", " |\n\n"} {
		if _, e := parseDrawing(s); e == nil {
			t.Fatalf("accepted %q", s)
		}
	}
}
func TestRemoteClassification(t *testing.T) {
	if !needsServer(Action{Type: "sequence", Steps: []Action{{Type: "server.request"}}}) {
		t.Fatal("sequence not routed remotely")
	}
	if needsServer(Action{Type: "volume"}) {
		t.Fatal("local command routed remotely")
	}
}
func TestSeparateWorkers(t *testing.T) {
	remote := make(chan invocation, 1)
	local := make(chan invocation, 1)
	remote <- invocation{1, "slow", Action{Type: "delay", Args: []string{"400"}}}
	local <- invocation{2, "fast", Action{Type: "delay", Args: []string{"0"}}}
	close(remote)
	close(local)
	remoteDone := make(chan struct{})
	localDone := make(chan struct{})
	go func() { runActionWorker(remote, "remote"); close(remoteDone) }()
	go func() { runActionWorker(local, "local"); close(localDone) }()
	select {
	case <-localDone:
	case <-time.After(200 * time.Millisecond):
		t.Fatal("local worker delayed")
	}
	select {
	case <-remoteDone:
		t.Fatal("remote delay did not run")
	default:
	}
	<-remoteDone
}
