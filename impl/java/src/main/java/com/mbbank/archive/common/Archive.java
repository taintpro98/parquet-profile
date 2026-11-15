package com.mbbank.archive.common;

import java.nio.file.Path;
import java.util.Collection;
import java.util.List;

/**
 * One storage option. Each implementation is one file under the archive package and registers itself
 * in META-INF/services/com.mbbank.archive.common.Archive (standard Java ServiceLoader).
 */
public interface Archive {

    /** Option name, used as --impl and as part of the result folder (java-NAME). */
    String name();

    /** File extension of the archive, without the dot. */
    String extension();

    /** Writes every row of the cursor to {@code out} and returns the row count. */
    long dump(RowCursor rows, Path out) throws Exception;

    /** Returns the complete record of every reference that exists, in any order. */
    List<Row> find(Path archive, Collection<String> references) throws Exception;
}
