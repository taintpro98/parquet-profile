package com.mbbank.archive.common;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.Set;

/**
 * Splits a cursor ordered by (day of CREATED_AT, REFERENCE) into one cursor per day. Each call to
 * {@link #nextDay()} starts the next day; {@link #next()} then returns false at the end of that day.
 */
final class DaySplitter implements RowCursor {

    private final RowCursor source;
    private final Set<LocalDate> written = new HashSet<>();
    private boolean pending; // the source is on a row that belongs to the next day
    private boolean first;
    private LocalDate day;

    DaySplitter(RowCursor source) {
        this.source = source;
    }

    /** Moves to the next day, or returns false when the source is exhausted. */
    boolean nextDay() throws Exception {
        if (!pending && !source.next()) {
            return false;
        }
        day = source.createdAt().toLocalDate();
        if (!written.add(day)) {
            throw new IllegalStateException("Rows are not ordered by day: " + day + " appears twice");
        }
        pending = false;
        first = true;
        return true;
    }

    LocalDate day() {
        return day;
    }

    @Override
    public boolean next() throws Exception {
        if (first) {
            first = false;
            return true;
        }
        if (pending || !source.next()) {
            return false;
        }
        if (!source.createdAt().toLocalDate().equals(day)) {
            pending = true;
            return false;
        }
        return true;
    }

    @Override
    public long id() throws Exception {
        return source.id();
    }

    @Override
    public String reference() throws Exception {
        return source.reference();
    }

    @Override
    public LocalDateTime createdAt() throws Exception {
        return source.createdAt();
    }

    @Override
    public int textColumns() {
        return source.textColumns();
    }

    @Override
    public String text(int column) throws Exception {
        return source.text(column);
    }
}
