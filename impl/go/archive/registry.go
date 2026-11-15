// Package archive holds the Go storage options. Each option is one file that registers itself in
// init(), so adding an option means adding exactly one file.
package archive

import (
	"fmt"
	"sort"

	"parquetprofile/common"
)

var all = map[string]func() common.Archive{}

// Register is called from each option file's init().
func Register(name string, factory func() common.Archive) {
	if _, dup := all[name]; dup {
		panic("duplicate option " + name)
	}
	all[name] = factory
}

func Get(name string) (common.Archive, error) {
	if factory, ok := all[name]; ok {
		return factory(), nil
	}
	return nil, fmt.Errorf("unknown option %s, known: %v", name, Names())
}

func Names() []string {
	names := make([]string, 0, len(all))
	for name := range all {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}
