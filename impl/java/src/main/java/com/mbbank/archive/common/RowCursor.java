package com.mbbank.archive.common;

import java.time.LocalDateTime;

/**
 * The rows DUMP reads, in REFERENCE order. A cursor rather than row objects, so a writer can copy
 * each value straight from the JDBC buffer without allocating a record per row.
 */
public interface RowCursor {

    /** Moves to the next row; false at the end. */
    boolean next() throws Exception;

    long id() throws Exception;

    String reference() throws Exception;

    LocalDateTime createdAt() throws Exception;

    int textColumns();

    /** Text column 1..{@link #textColumns()}, or null for NULL. */
    String text(int column) throws Exception;
}
