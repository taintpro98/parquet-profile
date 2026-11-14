// Command archive is the Go side of the shared command line:
//
//	list
//	dump --impl NAME --archive DIR
//	find --impl NAME --archive DIR --refs LOOKUPS --out RESULT.jsonl
//
// DIR holds one file per day. LOOKUPS has one "REFERENCE,YYYY-MM-DD HH:MM:SS.ffffff" per line.
// The last stdout line is JSON for the runner: {"rows":N} after dump, {"found":N} after find.
package main

import (
	"fmt"
	"os"
	"strings"

	"parquetprofile/archive"
	"parquetprofile/common"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		usage()
	}
	options := map[string]string{}
	for i := 1; i < len(args); i += 2 {
		if !strings.HasPrefix(args[i], "--") || i+1 >= len(args) {
			usage()
		}
		options[args[i][2:]] = args[i+1]
	}
	required := func(name string) string {
		v, ok := options[name]
		if !ok {
			usage()
		}
		return v
	}
	switch args[0] {
	case "list":
		for _, name := range archive.Names() {
			a, _ := archive.Get(name)
			fmt.Printf("%s\t%s\n", name, a.Extension())
		}
	case "dump":
		a, err := archive.Get(required("impl"))
		if err != nil {
			return err
		}
		rows, err := common.Dump(a, common.OracleConfigFromEnv(), required("archive"))
		if err != nil {
			return err
		}
		fmt.Printf("{\"rows\":%d}\n", rows)
	case "find":
		a, err := archive.Get(required("impl"))
		if err != nil {
			return err
		}
		lookups, err := common.ReadLookups(required("refs"))
		if err != nil {
			return err
		}
		found, err := common.Find(a, required("archive"), lookups, required("out"))
		if err != nil {
			return err
		}
		fmt.Printf("{\"found\":%d}\n", found)
	default:
		usage()
	}
	return nil
}

func usage() {
	fmt.Fprintln(os.Stderr, `Usage:
  list
  dump --impl NAME --archive DIR
  find --impl NAME --archive DIR --refs LOOKUPS --out RESULT.jsonl`)
	os.Exit(2)
}
