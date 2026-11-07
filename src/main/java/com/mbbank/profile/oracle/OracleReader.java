package com.mbbank.profile.oracle;

import com.mbbank.profile.config.DatabaseConfig;
import com.mbbank.profile.parquet.ParquetArchiveWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

/** Streams the Oracle table into one Parquet file, one ResultSet row at a time. */
public final class OracleReader {

    private static final long REPEAT_ID_STEP = 1_000_000_000L;

    private final DatabaseConfig config;

    public OracleReader(DatabaseConfig config) {
        this.config = config;
    }

    /**
     * Writes to "output.tmp" and renames it only after the Parquet writer closed successfully, so a
     * failed run never leaves a file that looks like benchmark output.
     *
     * @return the number of rows written
     */
    public long writeToParquet() throws SQLException, IOException {
        Path output = config.parquetOutput();
        Path tmp = output.resolveSibling(output.getFileName() + ".tmp");
        if (output.toAbsolutePath().getParent() != null) {
            Files.createDirectories(output.toAbsolutePath().getParent());
        }
        try {
            long rows = stream(tmp);
            Files.move(tmp, output, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            return rows;
        } finally {
            Files.deleteIfExists(tmp);
        }
    }

    private long stream(Path target) throws SQLException, IOException {
        try (Connection connection = DriverManager.getConnection(
                config.oracleUrl(), config.oracleUsername(), config.oraclePassword())) {
            useBinarySort(connection);
            boolean repeated = config.oracleRepeat() > 1;
            try (PreparedStatement statement = connection.prepareStatement(
                            repeated ? repeatedSelectSql() : selectSql(),
                            ResultSet.TYPE_FORWARD_ONLY,
                            ResultSet.CONCUR_READ_ONLY);
                    ParquetArchiveWriter writer = new ParquetArchiveWriter(target, config)) {
                statement.setFetchSize(config.oracleFetchSize());
                long rows = 0;
                for (int pass = 0; pass < config.oracleRepeat(); pass++) {
                    if (repeated) {
                        statement.setInt(1, pass);
                        statement.setInt(2, pass);
                    }
                    try (ResultSet resultSet = statement.executeQuery()) {
                        while (resultSet.next()) {
                            writer.write(resultSet);
                            if (++rows % config.progressInterval() == 0) {
                                System.out.printf("Processed %,d rows%n", rows);
                            }
                        }
                    }
                }
                return rows;
            }
        }
    }

    /**
     * The thin driver derives NLS_LANGUAGE from the JVM locale, and some languages imply a
     * linguistic NLS_SORT. Parquet statistics compare UTF-8 bytes, so REFERENCE must be binary
     * sorted or row-group min/max ranges would overlap.
     */
    private static void useBinarySort(Connection connection) throws SQLException {
        try (Statement statement = connection.createStatement()) {
            statement.execute("ALTER SESSION SET NLS_SORT = BINARY");
        }
    }

    /** ID, REFERENCE and CREATED_AT followed by the text columns; the column order is what ParquetArchiveWriter reads. */
    private String selectSql() {
        if (!config.oracleQuery().isBlank()) {
            return config.oracleQuery();
        }
        return "SELECT ID, REFERENCE, CREATED_AT" + textColumns()
                + " FROM " + config.oracleTable() + " ORDER BY REFERENCE ASC";
    }

    /**
     * Re-reads the table once per pass to simulate a bigger source. The pass number prefixes
     * REFERENCE, so the passes concatenate into one file that stays sorted and has unique
     * references. Oracle still produces every row and sorts every pass.
     */
    private String repeatedSelectSql() {
        if (!config.oracleQuery().isBlank()) {
            throw new IllegalArgumentException("oracle.repeat cannot be combined with oracle.query");
        }
        return "SELECT ID + ? * " + REPEAT_ID_STEP + ", LPAD(?, 4, '0') || REFERENCE, CREATED_AT" + textColumns()
                + " FROM " + config.oracleTable() + " ORDER BY REFERENCE ASC";
    }

    private String textColumns() {
        StringBuilder columns = new StringBuilder();
        for (int i = 1; i <= config.oracleTextColumns(); i++) {
            columns.append(String.format(", TEXT_%02d", i));
        }
        return columns.toString();
    }
}
