package com.mbbank.archive.common;

import java.time.LocalDateTime;

/** One complete record as FIND returns it. A NULL text column is a Java null. */
public record Row(long id, String reference, LocalDateTime createdAt, String[] texts) {}
