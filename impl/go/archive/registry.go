package archive

import (
	"fmt"
	"sort"

	"parquetprofile/common"
)

// All lists every Go option by name. A new option is one file in this package plus one line here.
var All = map[string]func() common.Archive{
	"parquet-duckdb": func() common.Archive { return ParquetDuckdb{} },
	"arrow-zstd":     func() common.Archive { return ArrowZstd{} },
	"rowblock-zstd":  func() common.Archive { return RowblockZstd{} },
}

func Get(name string) (common.Archive, error) {
	if make, ok := All[name]; ok {
		return make(), nil
	}
	return nil, fmt.Errorf("unknown option %s, known: %v", name, Names())
}

func Names() []string {
	var names []string
	for name := range All {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}
