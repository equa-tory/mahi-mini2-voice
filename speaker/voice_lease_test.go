package main

import (
	"testing"
	"time"
)

func TestVoiceLeaseExclusiveFailoverAndStableRecovery(t *testing.T) {
	voiceOwner = voiceLease{}
	primaryCandidate = ""
	primarySince = time.Time{}
	primarySeen = time.Time{}
	t.Cleanup(func() { voiceOwner = voiceLease{}; primaryCandidate = "" })
	now := time.Now()
	acquire := func(node, role string, at time.Time) bool {
		t.Helper()
		result, err := leaseOperation([]string{"acquire", node, role, "http://127.0.0.1:8787", "test"}, at)
		if err != nil {
			t.Fatal(err)
		}
		return result.(map[string]any)["granted"].(bool)
	}
	if !acquire("main", "primary", now) || acquire("pi", "standby", now) {
		t.Fatal("two owners")
	}
	now = now.Add(9 * time.Second)
	if !acquire("pi", "standby", now) {
		t.Fatal("standby did not take over")
	}
	for i := 0; i < 60; i++ {
		at := now.Add(time.Duration(i) * time.Second)
		if acquire("main", "primary", at) {
			t.Fatal("primary preempted live lease")
		}
		if !acquire("pi", "standby", at) {
			t.Fatal("standby yielded before 60s")
		}
	}
	now = now.Add(60 * time.Second)
	if acquire("pi", "standby", now) {
		t.Fatal("standby kept renewing after stability")
	}
	if acquire("main", "primary", now) {
		t.Fatal("primary overlapped draining standby")
	}
	for i := 1; i <= 8; i++ {
		acquire("main", "primary", now.Add(time.Duration(i)*time.Second))
	}
	if voiceOwner.Owner != "main" {
		t.Fatal("primary did not reclaim")
	}
	if acquire("pi", "standby", now.Add(8*time.Second)) {
		t.Fatal("standby overlapped primary")
	}
}
func TestVoiceLeaseInterruptedRecoveryResetsStability(t *testing.T) {
	voiceOwner = voiceLease{}
	primaryCandidate = ""
	now := time.Now()
	leaseOperation([]string{"acquire", "main", "primary", "http://127.0.0.1:8787", "gpu"}, now)
	leaseOperation([]string{"acquire", "main", "primary", "http://127.0.0.1:8787", "gpu"}, now.Add(70*time.Second))
	if !primarySince.Equal(now.Add(70 * time.Second)) {
		t.Fatal("health gap did not reset recovery")
	}
	voiceOwner = voiceLease{}
	primaryCandidate = ""
}
