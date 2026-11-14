package com.mbbank.archive.common;

/** Every setting comes from an environment variable, so Java and Go are configured the same way. */
public final class Settings {

    private Settings() {}

    public static String string(String name, String defaultValue) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? defaultValue : value;
    }

    public static int integer(String name, int defaultValue) {
        return Integer.parseInt(string(name, Integer.toString(defaultValue)));
    }

    public static long number(String name, long defaultValue) {
        return Long.parseLong(string(name, Long.toString(defaultValue)));
    }

    public static boolean flag(String name, boolean defaultValue) {
        return Boolean.parseBoolean(string(name, Boolean.toString(defaultValue)));
    }
}
