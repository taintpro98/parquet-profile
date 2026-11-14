package com.mbbank.archive.common;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;

/** Streams the Oracle table in REFERENCE order as a {@link RowCursor}. */
public final class OracleSource implements RowCursor, AutoCloseable {

    private static final long REPEAT_ID_STEP = 1_000_000_000L;

    /** Connection and query settings, the same names and defaults as the Go implementation. */
    public record Config(
            String url,
            String username,
            String password,
            String table,
            String query,
            int repeat,
            int fetchSize,
            int textColumns,
            long progressInterval) {

        public static Config fromEnv() {
            return new Config(
                    Settings.string("ORACLE_URL", "jdbc:oracle:thin:@//localhost:1521/FREEPDB1"),
                    Settings.string("ORACLE_USERNAME", "benchmark"),
                    Settings.string("ORACLE_PASSWORD", "benchmark"),
                    Settings.string("ORACLE_TABLE", "ARCHIVE_PROFILE_DATA"),
                    Settings.string("ORACLE_QUERY", ""),
                    Settings.integer("ORACLE_REPEAT", 1),
                    Settings.integer("ORACLE_FETCH_SIZE", 1000),
                    Settings.integer("ORACLE_TEXT_COLUMNS", 70),
                    Settings.number("PROGRESS_INTERVAL", 500_000));
        }

        public Config withTable(String table) {
            return new Config(url, username, password, table, query, repeat, fetchSize, textColumns, progressInterval);
        }
    }

    private final Config config;
    private final Connection connection;
    private final PreparedStatement statement;
    private ResultSet resultSet;
    private int pass;
    private long rows;

    public OracleSource(Config config) throws SQLException {
        if (config.repeat() < 1 || config.repeat() > 9999) {
            throw new IllegalArgumentException("ORACLE_REPEAT must be 1 to 9999");
        }
        this.config = config;
        this.connection = DriverManager.getConnection(config.url(), config.username(), config.password());
        try {
            useBinarySort(connection);
            this.statement = connection.prepareStatement(
                    config.repeat() > 1 ? repeatedSelectSql() : selectSql(),
                    ResultSet.TYPE_FORWARD_ONLY,
                    ResultSet.CONCUR_READ_ONLY);
            statement.setFetchSize(config.fetchSize());
        } catch (SQLException | RuntimeException e) {
            connection.close();
            throw e;
        }
    }

    @Override
    public boolean next() throws SQLException {
        while (true) {
            if (resultSet == null) {
                if (pass == config.repeat()) {
                    return false;
                }
                if (config.repeat() > 1) {
                    statement.setInt(1, pass);
                    statement.setInt(2, pass);
                }
                pass++;
                resultSet = statement.executeQuery();
            }
            if (resultSet.next()) {
                if (++rows % config.progressInterval() == 0) {
                    System.err.printf("Processed %,d rows%n", rows);
                }
                return true;
            }
            resultSet.close();
            resultSet = null;
        }
    }

    @Override
    public long id() throws SQLException {
        return resultSet.getLong(1);
    }

    @Override
    public String reference() throws SQLException {
        return resultSet.getString(2);
    }

    /** Read as LocalDateTime, so the JVM time zone cannot shift it. */
    @Override
    public LocalDateTime createdAt() throws SQLException {
        return resultSet.getObject(3, LocalDateTime.class);
    }

    @Override
    public int textColumns() {
        return config.textColumns();
    }

    @Override
    public String text(int column) throws SQLException {
        return resultSet.getString(3 + column);
    }

    @Override
    public void close() throws SQLException {
        connection.close();
    }

    /**
     * The thin driver derives NLS_LANGUAGE from the JVM locale, and some languages imply a
     * linguistic NLS_SORT. Archives compare UTF-8 bytes, so REFERENCE must be binary sorted.
     */
    private static void useBinarySort(Connection connection) throws SQLException {
        try (Statement alter = connection.createStatement()) {
            alter.execute("ALTER SESSION SET NLS_SORT = BINARY");
        }
    }

    /**
     * ID, REFERENCE, CREATED_AT, then the text columns: the order the cursor reads by position.
     * Ordered by day first, because every day goes to its own file, then by REFERENCE within the day.
     */
    private String selectSql() {
        if (!config.query().isBlank()) {
            return config.query();
        }
        return "SELECT ID, REFERENCE, CREATED_AT" + textColumnList()
                + " FROM " + config.table() + " ORDER BY TRUNC(CREATED_AT), REFERENCE";
    }

    /**
     * Re-reads the table once per pass to simulate a bigger day. The pass number prefixes
     * REFERENCE, so the passes concatenate into one sorted file with unique references. The table
     * must hold a single day, or the passes would interleave days.
     */
    private String repeatedSelectSql() {
        if (!config.query().isBlank()) {
            throw new IllegalArgumentException("ORACLE_REPEAT cannot be combined with ORACLE_QUERY");
        }
        return "SELECT ID + ? * " + REPEAT_ID_STEP + ", LPAD(?, 4, '0') || REFERENCE, CREATED_AT" + textColumnList()
                + " FROM " + config.table() + " ORDER BY TRUNC(CREATED_AT), REFERENCE";
    }

    private String textColumnList() {
        StringBuilder columns = new StringBuilder();
        for (int i = 1; i <= config.textColumns(); i++) {
            columns.append(String.format(", TEXT_%02d", i));
        }
        return columns.toString();
    }
}
