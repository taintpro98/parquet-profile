// Package common holds what every Go option shares: settings, the Oracle source, the row model,
// the canonical result format and the dump/find steps around an Archive.
package common

import (
	"os"
	"strconv"
	"strings"
)

// Every setting comes from an environment variable, so Java and Go are configured the same way.

func String(name, def string) string {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		return v
	}
	return def
}

func Int(name string, def int64) int64 {
	v, err := strconv.ParseInt(String(name, strconv.FormatInt(def, 10)), 10, 64)
	if err != nil {
		panic(name + ": " + err.Error())
	}
	return v
}

func Bool(name string, def bool) bool {
	v, err := strconv.ParseBool(String(name, strconv.FormatBool(def)))
	if err != nil {
		panic(name + ": " + err.Error())
	}
	return v
}
