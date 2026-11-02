package main

import (
	"errors"
	"net/url"
	"sync"
	"time"
)

type voiceLease struct {
	Owner   string    `json:"owner"`
	URL     string    `json:"url"`
	Backend string    `json:"backend"`
	Until   time.Time `json:"expires_at"`
}

var voiceLeaseMu sync.Mutex
var voiceOwner voiceLease
var primaryCandidate string
var primarySince, primarySeen time.Time

func leaseSnapshot() voiceLease {
	voiceLeaseMu.Lock()
	defer voiceLeaseMu.Unlock()
	if !time.Now().Before(voiceOwner.Until) {
		return voiceLease{}
	}
	return voiceOwner
}
func leaseOperation(args []string, now time.Time) (any, error) {
	voiceLeaseMu.Lock()
	defer voiceLeaseMu.Unlock()
	if len(args) == 1 && args[0] == "status" {
		if !now.Before(voiceOwner.Until) {
			return voiceLease{}, nil
		}
		return voiceOwner, nil
	}
	if len(args) == 2 && args[0] == "check" {
		return now.Before(voiceOwner.Until) && voiceOwner.Owner == args[1], nil
	}
	if len(args) == 2 && args[0] == "release" {
		if voiceOwner.Owner == args[1] {
			voiceOwner = voiceLease{}
		}
		return true, nil
	}
	if len(args) != 5 || (args[0] != "acquire" && args[0] != "renew") || (args[2] != "primary" && args[2] != "standby") || len(args[1]) < 1 || len(args[1]) > 100 {
		return nil, errors.New("voice lease acquire|renew NODE primary|standby URL BACKEND; status|check NODE|release NODE")
	}
	u, e := url.Parse(args[3])
	if e != nil || u.Scheme != "http" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || len(args[4]) > 80 {
		return nil, errors.New("invalid voice endpoint")
	}
	if args[2] == "primary" {
		if primaryCandidate != args[1] || now.Sub(primarySeen) > 3*time.Second {
			primarySince = now
			primaryCandidate = args[1]
		}
		primarySeen = now
	}
	stable := primaryCandidate != "" && now.Sub(primarySeen) <= 3*time.Second && now.Sub(primarySince) >= 60*time.Second
	live := now.Before(voiceOwner.Until)
	granted := false
	if live && voiceOwner.Owner == args[1] {
		// Keep the old lease valid while its owner drains; do not extend it.
		granted = !(args[2] == "standby" && stable)
	} else if !live {
		granted = !(args[2] == "standby" && stable)
	}
	if granted {
		voiceOwner = voiceLease{args[1], args[3], args[4], now.Add(8 * time.Second)}
	}
	return map[string]any{"granted": granted, "lease": voiceOwner, "primary_stable": stable}, nil
}
func voiceLeaseCommand(args []string) (any, error, bool) {
	if len(args) < 2 || args[0] != "voice" || args[1] != "lease" {
		return nil, nil, false
	}
	v, e := leaseOperation(args[2:], time.Now())
	return v, e, true
}
