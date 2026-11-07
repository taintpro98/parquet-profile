package com.mbbank.profile.config;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.file.Path;
import java.util.Properties;

/**
 * Benchmark settings from application.properties. A -Dkey=value system property overrides the
 * file, and ORACLE_URL / ORACLE_USERNAME / ORACLE_PASSWORD override everything else.
 */
public record DatabaseConfig(
        String oracleUrl,
        String oracleUsername,
        String oraclePassword,
        String oracleTable,
        String oracleQuery,
        int oracleRepeat,
        int oracleFetchSize,
        int oracleTextColumns,
        Path parquetOutput,
        String parquetCompression,
        int parquetRowGroupRows,
        long parquetRowGroupBytes,
        long progressInterval,
        int duckdbThreads,
        boolean duckdbExternalFileCache) {

    public static DatabaseConfig load() {
        Properties properties = new Properties();
        try (InputStream in = DatabaseConfig.class.getResourceAsStream("/application.properties")) {
            properties.load(in);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        properties.putAll(System.getProperties());

        return new DatabaseConfig(
                envOr("ORACLE_URL", properties, "oracle.url"),
                envOr("ORACLE_USERNAME", properties, "oracle.username"),
                envOr("ORACLE_PASSWORD", properties, "oracle.password"),
                required(properties, "oracle.table"),
                required(properties, "oracle.query"),
                Integer.parseInt(required(properties, "oracle.repeat")),
                Integer.parseInt(required(properties, "oracle.fetch-size")),
                Integer.parseInt(required(properties, "oracle.text-columns")),
                Path.of(required(properties, "parquet.output")),
                required(properties, "parquet.compression"),
                Integer.parseInt(required(properties, "parquet.row-group-rows")),
                Long.parseLong(required(properties, "parquet.row-group-bytes")),
                Long.parseLong(required(properties, "progress.interval")),
                Integer.parseInt(required(properties, "duckdb.threads")),
                Boolean.parseBoolean(required(properties, "duckdb.external-file-cache")));
    }

    private static String envOr(String envName, Properties properties, String key) {
        String value = System.getenv(envName);
        return value != null ? value : required(properties, key);
    }

    private static String required(Properties properties, String key) {
        String value = properties.getProperty(key);
        if (value == null) {
            throw new IllegalArgumentException("Missing configuration: " + key);
        }
        return value;
    }
}
