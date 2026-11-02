package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"time"
)

type weatherHour struct {
	Time        int64   `json:"t"`
	Temperature float64 `json:"c"`
	Code        int     `json:"code"`
	Rain        int     `json:"rain"`
}
type weatherCache struct {
	Source   string        `json:"source"`
	Location string        `json:"location"`
	Fetched  int64         `json:"fetched"`
	Current  weatherHour   `json:"current"`
	Hours    []weatherHour `json:"hours"`
}

func validateWeather(w weatherCache, now time.Time) error {
	if len(w.Location) == 0 || len(w.Location) > 100 || len(w.Hours) < 1 || len(w.Hours) > 73 {
		return errors.New("invalid weather cache size")
	}
	if w.Fetched <= 0 || w.Fetched > now.Unix()+300 {
		return errors.New("invalid cache timestamp")
	}
	previous := int64(0)
	for _, h := range append([]weatherHour{w.Current}, w.Hours...) {
		if h.Time <= 0 || math.IsNaN(h.Temperature) || math.IsInf(h.Temperature, 0) || h.Temperature < -99 || h.Temperature > 100 || h.Rain < -1 || h.Rain > 100 || h.Code < -1 || h.Code > 99 {
			return errors.New("invalid forecast values")
		}
	}
	for _, h := range w.Hours {
		if h.Time <= previous {
			return errors.New("forecast hours must be increasing")
		}
		previous = h.Time
	}
	return nil
}
func selectWeather(w weatherCache, now time.Time) (weatherHour, bool, error) {
	if now.Unix() < w.Fetched-300 {
		return weatherHour{}, false, errors.New("speaker clock predates weather cache")
	}
	age := now.Unix() - w.Current.Time
	if age >= 0 && age < 3600 {
		return w.Current, false, nil
	}
	for _, h := range w.Hours {
		if now.Unix() >= h.Time && now.Unix() < h.Time+3600 {
			return h, true, nil
		}
	}
	return weatherHour{}, false, errors.New("cached forecast has expired")
}
func readWeather() (weatherCache, error) {
	var w weatherCache
	b, e := os.ReadFile(dir + "weather.json")
	if e != nil {
		return w, errors.New("no cached weather yet")
	}
	if json.Unmarshal(b, &w) != nil {
		return w, errors.New("invalid weather cache")
	}
	if e := validateWeather(w, time.Now()); e != nil {
		return w, e
	}
	return w, nil
}
func weatherSnapshot(now time.Time) (map[string]any, error) {
	w, e := readWeather()
	if e != nil {
		return nil, e
	}
	h, forecast, e := selectWeather(w, now)
	if e != nil {
		return nil, e
	}
	age := now.Unix() - w.Fetched
	if age < 0 {
		age = 0
	}
	var code any = h.Code
	var rain any = h.Rain
	if h.Code < 0 {
		code = nil
	}
	if h.Rain < 0 {
		rain = nil
	}
	return map[string]any{"location": w.Location, "temperature_c": h.Temperature, "weather_code": code, "rain_probability": rain, "source": w.Source, "forecast": forecast, "cached_at": w.Fetched, "age_seconds": age, "stale": age > 10800, "valid_until": w.Hours[len(w.Hours)-1].Time + 3600}, nil
}
func weatherCommand(args []string) (any, error, bool) {
	if len(args) == 0 || args[0] != "weather" {
		return nil, nil, false
	}
	if len(args) == 2 && args[1] == "status" {
		v, e := weatherSnapshot(time.Now())
		return v, e, true
	}
	if read(dir+"default") != "local" {
		return nil, errors.New("local mode required"), true
	}
	if len(args) == 3 && args[1] == "update" {
		var w weatherCache
		if e := json.Unmarshal([]byte(args[2]), &w); e != nil {
			return nil, errors.New("invalid weather JSON"), true
		}
		if e := validateWeather(w, time.Now()); e != nil {
			return nil, e, true
		}
		b, _ := json.Marshal(w)
		return "weather cache saved", atomicWrite(dir+"weather.json", string(b)), true
	}
	if len(args) == 2 && args[1] == "refresh" {
		return "weather refreshed", requestServer([]string{"weather_refresh"}), true
	}
	if (len(args) == 2 || len(args) == 3) && args[1] == "show" {
		seconds := displaySeconds()
		if len(args) == 3 {
			n, e := integer(args[2], 1, 86400)
			if e != nil {
				return nil, e, true
			}
			seconds = n
		}
		v, e := weatherSnapshot(time.Now())
		if e != nil {
			overlay("old")
			return nil, e, true
		}
		text := fmt.Sprintf("%3dC", int(math.Round(v["temperature_c"].(float64))))
		_, e = dispatch([]string{"clock", "text", text, fmt.Sprint(seconds)})
		if e == nil {
			startMotion("pulse", 2)
			if v["stale"].(bool) {
				overlay("old")
			}
		}
		return v, e, true
	}
	return nil, errors.New("weather show [seconds]|status|refresh"), true
}
