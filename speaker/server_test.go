package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestServerRequest(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer secret" {
			t.Error("missing authentication")
		}
		var body struct {
			Action string
			Args   []string
		}
		if e := json.NewDecoder(r.Body).Decode(&body); e != nil || body.Action != "hello" {
			t.Error(body, e)
		}
		w.Write([]byte("{\"ok\":true}"))
	}))
	defer s.Close()
	if e := callServer(serverSettings{s.URL, "secret"}, []string{"hello"}); e != nil {
		t.Fatal(e)
	}
}
func TestServerFailure(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(502) }))
	if e := callServer(serverSettings{s.URL, "secret"}, []string{"hello"}); e == nil {
		t.Fatal("accepted failure")
	}
	s.Close()
	if e := callServer(serverSettings{s.URL, "secret"}, []string{"hello"}); e == nil {
		t.Fatal("accepted offline server")
	}
}
