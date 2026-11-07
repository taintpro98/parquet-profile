package com.mbbank.profile.profiler;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * One lookup result. The match holds every column of the record in file order, with Java null for
 * a NULL value, and is empty when nothing was found. Nanos is {@link #NOT_TIMED} when the reference
 * was found by a query shared with other references.
 */
public record Lookup(String reference, long nanos, Map<String, Object> match) {

    public static final long NOT_TIMED = -1;

    /** A record is too wide for the terminal and the chart, so these stand in for it there. */
    private static final List<String> SHOWN_COLUMNS = List.of("id", "created_at", "text_02", "text_03");

    public boolean found() {
        return !match.isEmpty();
    }

    /** The columns shown in the terminal and the chart. */
    public Map<String, String> shown() {
        Map<String, String> shown = new LinkedHashMap<>();
        for (String column : SHOWN_COLUMNS) {
            if (match.containsKey(column)) {
                shown.put(column, match.get(column) == null ? "NULL" : match.get(column).toString());
            }
        }
        return shown;
    }
}
