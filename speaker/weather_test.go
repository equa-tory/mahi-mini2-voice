package main

import (
	"testing"
	"time"
)

func TestCachedWeatherSelection(t *testing.T) {
	now := time.Unix(1800000000, 0)
	w := weatherCache{Location: "Podolsk", Fetched: now.Unix(), Current: weatherHour{Time: now.Unix(), Temperature: 5}, Hours: []weatherHour{{Time: now.Unix(), Temperature: 6}, {Time: now.Add(time.Hour).Unix(), Temperature: 7}}}
	if e := validateWeather(w, now); e != nil {
		t.Fatal(e)
	}
	h, forecast, e := selectWeather(w, now)
	if e != nil || forecast || h.Temperature != 5 {
		t.Fatal(h, forecast, e)
	}
	h, forecast, e = selectWeather(w, now.Add(90*time.Minute))
	if e != nil || !forecast || h.Temperature != 7 {
		t.Fatal(h, forecast, e)
	}
	if _, _, e = selectWeather(w, now.Add(3*time.Hour)); e == nil {
		t.Fatal("expired forecast accepted")
	}
	if _, _, e = selectWeather(w, now.Add(-time.Hour)); e == nil {
		t.Fatal("bad clock accepted")
	}
	w.Hours[1].Time = w.Hours[0].Time
	if validateWeather(w, now) == nil {
		t.Fatal("unordered forecast accepted")
	}
}
