package state

type Op struct {
	Key              string
	Value            string
	ExpectedVersion  int
}

type Store struct {
	data map[string]string
	ver  map[string]int
}

func New() *Store {
	return &Store{
		data: map[string]string{},
		ver:  map[string]int{},
	}
}

func (s *Store) Get(key string) (string, int, bool) {
	value, ok := s.data[key]
	return value, s.ver[key], ok
}

func (s *Store) Put(key, value string, expectedVersion int) bool {
	// BUG: ignores expectedVersion, last write wins.
	s.data[key] = value
	s.ver[key] = s.ver[key] + 1
	return true
}

func (s *Store) Apply(ops []Op) []bool {
	accepted := make([]bool, len(ops))
	for i, op := range ops {
		accepted[i] = s.Put(op.Key, op.Value, op.ExpectedVersion)
	}
	return accepted
}
