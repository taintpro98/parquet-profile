package com.mbbank.archive.archive;

import com.mbbank.archive.common.Archive;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.Supplier;

/** Every Java option by name. A new option is one file in this package plus one line here. */
public final class Archives {

    public static final Map<String, Supplier<Archive>> ALL = new TreeMap<>(Map.of(
            "parquet-duckdb", ParquetDuckdb::new,
            "arrow-zstd", ArrowZstd::new,
            "rowblock-zstd", RowblockZstd::new));

    private Archives() {}

    public static Archive get(String name) {
        Supplier<Archive> archive = ALL.get(name);
        if (archive == null) {
            throw new IllegalArgumentException("Unknown option: " + name + ", known: " + ALL.keySet());
        }
        return archive.get();
    }
}
