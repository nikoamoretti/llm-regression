package functional

import (
	"testing"

	state "example.com/state"
)

func TestRejectsStaleWrite(t *testing.T) {
	s := state.New()
	if !s.Put("acct", "v1", 0) {
		t.Fatal("first insert should succeed")
	}
	if s.Put("acct", "stale", 0) {
		t.Fatal("stale writer must be rejected")
	}
	value, ver, ok := s.Get("acct")
	if !ok || value != "v1" || ver != 1 {
		t.Fatalf("got %q ver=%d ok=%v", value, ver, ok)
	}
}

func TestApplySchedule(t *testing.T) {
	s := state.New()
	ops := []state.Op{
		{Key: "k", Value: "a", ExpectedVersion: 0},
		{Key: "k", Value: "b", ExpectedVersion: 0},
		{Key: "k", Value: "c", ExpectedVersion: 1},
	}
	got := s.Apply(ops)
	if got[0] != true || got[1] != false || got[2] != true {
		t.Fatalf("accepted=%v", got)
	}
	value, ver, _ := s.Get("k")
	if value != "c" || ver != 2 {
		t.Fatalf("got %q ver=%d", value, ver)
	}
}
