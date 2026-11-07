package com.mbbank.profile.duckdb;

import com.mbbank.profile.config.DatabaseConfig;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

/** Looks up one record in the Parquet file with embedded, in-memory DuckDB. */
public final class DuckDbReader implements AutoCloseable {

    private static final String SELECT = "SELECT * FROM read_parquet(?) ";

    private final Path parquetFile;
    private final Connection connection;

    /** Opens DuckDB, which also loads its native library. Do this outside the measured section. */
    public DuckDbReader(DatabaseConfig config) throws SQLException {
        this.parquetFile = config.parquetOutput();
        this.connection = DriverManager.getConnection("jdbc:duckdb:");
        try (Statement statement = connection.createStatement()) {
            statement.execute("SET threads = " + config.duckdbThreads());
            statement.execute("SET enable_external_file_cache = " + config.duckdbExternalFileCache());
        }
    }

    /**
     * Runs the lookup and reads every column of the match, if there is one.
     *
     * @return every column in file order, or an empty map when the reference does not exist
     */
    public Map<String, Object> findByReference(String reference) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(SELECT + "WHERE reference = ? LIMIT 1")) {
            statement.setString(1, parquetFile.toString());
            statement.setString(2, reference);
            try (ResultSet resultSet = statement.executeQuery()) {
                if (!resultSet.next()) {
                    return Collections.emptyMap();
                }
                return readRow(resultSet);
            }
        }
    }

    /**
     * Runs one query for all references and reads every column of every match.
     *
     * @return every column per reference; references that do not exist are absent
     */
    public Map<String, Map<String, Object>> findByReferences(Collection<String> references) throws SQLException {
        String sql = SELECT + "WHERE reference IN ("
                + String.join(",", Collections.nCopies(references.size(), "?")) + ")";
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, parquetFile.toString());
            int parameter = 2;
            for (String reference : references) {
                statement.setString(parameter++, reference);
            }
            Map<String, Map<String, Object>> matches = new HashMap<>();
            try (ResultSet resultSet = statement.executeQuery()) {
                while (resultSet.next()) {
                    matches.put(resultSet.getString("reference"), readRow(resultSet));
                }
            }
            return matches;
        }
    }

    /** Numbers stay numbers and NULL stays null; the rest becomes text, as the timestamp does. */
    private static Map<String, Object> readRow(ResultSet resultSet) throws SQLException {
        Map<String, Object> row = new LinkedHashMap<>();
        int columns = resultSet.getMetaData().getColumnCount();
        for (int column = 1; column <= columns; column++) {
            Object value = resultSet.getObject(column);
            String name = resultSet.getMetaData().getColumnName(column);
            row.put(name, value == null || value instanceof Number ? value : value.toString());
        }
        return row;
    }

    @Override
    public void close() throws SQLException {
        connection.close();
    }
}
