package com.mbbank.archive.archive;

import com.mbbank.archive.common.Archive;
import java.util.Map;
import java.util.ServiceLoader;
import java.util.TreeMap;

/** Every Java option, found through ServiceLoader. A new option is one file plus one services line. */
public final class Archives {

    public static final Map<String, Archive> ALL = new TreeMap<>();

    static {
        for (Archive archive : ServiceLoader.load(Archive.class)) {
            ALL.put(archive.name(), archive);
        }
    }

    private Archives() {}

    public static Archive get(String name) {
        Archive archive = ALL.get(name);
        if (archive == null) {
            throw new IllegalArgumentException("Unknown option: " + name + ", known: " + ALL.keySet());
        }
        return archive;
    }
}
