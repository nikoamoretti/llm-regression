package regression

import (
	"testing"

	state "example.com/state"
)

func TestFreshInsert(t *testing.T) {
	s := state.New()
	if !s.Put("n", "ok", 0) {
		t.Fatal("expected insert")
	}
	value, ver, ok := s.Get("n")
	if !ok || value != "ok" || ver != 1 {
		t.Fatalf("got %q %d %v", value, ver, ok)
	}
}
