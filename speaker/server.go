package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"time"
)

type serverSettings struct {
	URL   string `json:"url"`
	Token string `json:"token"`
}

func loadServerSettings() (serverSettings, error) {
	var settings serverSettings
	b, err := os.ReadFile(dir + "server.json")
	if err != nil {
		return settings, err
	}
	if json.Unmarshal(b, &settings) != nil || settings.URL == "" || settings.Token == "" {
		return settings, errors.New("invalid server settings")
	}
	return settings, nil
}

func voiceServerSettings() (serverSettings, error) {
	settings, err := loadServerSettings()
	if err != nil {
		return settings, err
	}
	if owner := leaseSnapshot(); owner.Owner != "" {
		settings.URL = owner.URL
	}
	return settings, nil
}
func assistantActive() bool {
	settings, err := voiceServerSettings()
	if err != nil {
		return false
	}
	req, err := http.NewRequest("GET", settings.URL+"/voice/status", nil)
	if err != nil {
		return false
	}
	req.Header.Set("Authorization", "Bearer "+settings.Token)
	client := http.Client{Timeout: 250 * time.Millisecond, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return false
	}
	var state struct {
		Phase     string `json:"phase"`
		UpdatedAt int64  `json:"updated_at"`
	}
	if json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&state) != nil {
		return false
	}
	if state.UpdatedAt > 0 && time.Now().Unix()-state.UpdatedAt > 8 {
		return false
	}
	return state.Phase == "listening" || state.Phase == "thinking" || state.Phase == "speaking"
}

func stopAssistant() error {
	settings, err := voiceServerSettings()
	if err != nil {
		return errors.New("local voice service is not configured")
	}
	req, err := http.NewRequest("POST", settings.URL+"/voice/interrupt", bytes.NewReader([]byte(`{}`)))
	if err != nil {
		return errors.New("invalid voice service URL")
	}
	req.Header.Set("Authorization", "Bearer "+settings.Token)
	req.Header.Set("Content-Type", "application/json")
	client := http.Client{Timeout: time.Second, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return errors.New("local voice service unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return errors.New("assistant stop failed")
	}
	return nil
}

func requestServer(args []string) error {
	settings, e := loadServerSettings()
	if e != nil {
		return errors.New("server runner is not configured")
	}
	return callServer(settings, args)
}
func callServer(settings serverSettings, args []string) error {
	if len(args) == 0 {
		return errors.New("server action name required")
	}
	body, _ := json.Marshal(map[string]any{"action": args[0], "args": args[1:]})
	req, e := http.NewRequest("POST", settings.URL+"/run", bytes.NewReader(body))
	if e != nil {
		return errors.New("invalid runner URL")
	}
	req.Header.Set("Authorization", "Bearer "+settings.Token)
	req.Header.Set("Content-Type", "application/json")
	client := http.Client{Timeout: 10 * time.Second, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	overlay("----")
	response, e := client.Do(req)
	if e != nil {
		return errors.New("server unavailable or timed out")
	}
	defer response.Body.Close()
	io.Copy(io.Discard, io.LimitReader(response.Body, 8192))
	if response.StatusCode != 200 {
		return errors.New("server action failed")
	}
	overlay("done")
	return nil
}
