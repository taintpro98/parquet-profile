package com.mbbank.archive.archive;

import com.mbbank.archive.common.Archive;
import com.mbbank.archive.common.Row;
import com.mbbank.archive.common.RowCursor;
import com.mbbank.archive.common.Settings;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import org.apache.hadoop.conf.Configuration;
import org.apache.parquet.hadoop.ParquetFileWriter;
import org.apache.parquet.hadoop.ParquetWriter;
import org.apache.parquet.hadoop.api.WriteSupport;
import org.apache.parquet.hadoop.metadata.CompressionCodecName;
import org.apache.parquet.io.LocalOutputFile;
import org.apache.parquet.io.api.Binary;
import org.apache.parquet.io.api.RecordConsumer;
import org.apache.parquet.schema.LogicalTypeAnnotation;
import org.apache.parquet.schema.LogicalTypeAnnotation.TimeUnit;
import org.apache.parquet.schema.MessageType;
import org.apache.parquet.schema.PrimitiveType.PrimitiveTypeName;
import org.apache.parquet.schema.Types;

/**
 * WRITE with parquet-java, streaming each row from the cursor straight into the column buffers.
 * FIND with embedded DuckDB, one {@code WHERE reference IN (...)} query for all references.
 *
 * <p>Settings: PARQUET_COMPRESSION (ZSTD), PARQUET_ROW_GROUP_ROWS (250000), PARQUET_ROW_GROUP_BYTES
 * (2 GiB, whichever limit is reached first), DUCKDB_THREADS (4), DUCKDB_EXTERNAL_FILE_CACHE (false).
 */
public final class ParquetDuckdb implements Archive {

    private static final int FIXED_COLUMNS = 3;

    @Override
    public String extension() {
        return "parquet";
    }

    // ---- WRITE ----

    @Override
    public long dump(RowCursor rows, Path out) throws Exception {
        MessageType schema = schema(rows.textColumns());
        long count = 0;
        try (ParquetWriter<RowCursor> writer = new Builder(out, schema)
                .withCompressionCodec(CompressionCodecName.fromConf(Settings.string("PARQUET_COMPRESSION", "ZSTD")))
                .withRowGroupRowCountLimit(Settings.integer("PARQUET_ROW_GROUP_ROWS", 250_000))
                .withRowGroupSize(Settings.number("PARQUET_ROW_GROUP_BYTES", 2L << 30))
                .withWriteMode(ParquetFileWriter.Mode.OVERWRITE)
                .build()) {
            while (rows.next()) {
                writer.write(rows);
                count++;
            }
        }
        return count;
    }

    private static MessageType schema(int textColumns) {
        Types.MessageTypeBuilder builder = Types.buildMessage();
        builder.required(PrimitiveTypeName.INT64).named("id");
        builder.required(PrimitiveTypeName.BINARY).as(LogicalTypeAnnotation.stringType()).named("reference");
        builder.required(PrimitiveTypeName.INT64)
                .as(LogicalTypeAnnotation.timestampType(false, TimeUnit.MICROS))
                .named("created_at");
        for (int i = 1; i <= textColumns; i++) {
            builder.optional(PrimitiveTypeName.BINARY)
                    .as(LogicalTypeAnnotation.stringType())
                    .named(String.format("text_%02d", i));
        }
        return builder.named("archive");
    }

    private static final class Builder extends ParquetWriter.Builder<RowCursor, Builder> {

        private final MessageType schema;

        Builder(Path file, MessageType schema) {
            super(new LocalOutputFile(file));
            this.schema = schema;
        }

        @Override
        protected Builder self() {
            return this;
        }

        @Override
        protected WriteSupport<RowCursor> getWriteSupport(Configuration conf) {
            return new CursorWriteSupport(schema);
        }
    }

    /** The cursor itself is the record type, so no row object exists between JDBC and Parquet. */
    private static final class CursorWriteSupport extends WriteSupport<RowCursor> {

        private final MessageType schema;
        private RecordConsumer consumer;

        CursorWriteSupport(MessageType schema) {
            this.schema = schema;
        }

        @Override
        public WriteContext init(Configuration configuration) {
            return new WriteContext(schema, Map.of());
        }

        @Override
        public void prepareForWrite(RecordConsumer recordConsumer) {
            this.consumer = recordConsumer;
        }

        @Override
        public void write(RowCursor row) {
            try {
                consumer.startMessage();
                consumer.startField("id", 0);
                consumer.addLong(row.id());
                consumer.endField("id", 0);
                consumer.startField("reference", 1);
                consumer.addBinary(Binary.fromString(row.reference()));
                consumer.endField("reference", 1);
                consumer.startField("created_at", 2);
                consumer.addLong(toEpochMicros(row.createdAt()));
                consumer.endField("created_at", 2);
                for (int field = FIXED_COLUMNS; field < schema.getFieldCount(); field++) {
                    String value = row.text(field - FIXED_COLUMNS + 1);
                    if (value != null) {
                        String name = schema.getFieldName(field);
                        consumer.startField(name, field);
                        consumer.addBinary(Binary.fromString(value));
                        consumer.endField(name, field);
                    }
                }
                consumer.endMessage();
            } catch (Exception e) {
                throw new IllegalStateException("Failed to read the current row", e);
            }
        }

        /** TIMESTAMP has no time zone, so the wall-clock value is stored as is. */
        private static long toEpochMicros(LocalDateTime value) {
            return value.toEpochSecond(ZoneOffset.UTC) * 1_000_000L + value.getNano() / 1_000L;
        }
    }

    // ---- FIND ----

    @Override
    public List<Row> find(Path archive, Collection<String> references) throws Exception {
        String sql = "SELECT * FROM read_parquet(?) WHERE reference IN ("
                + String.join(",", Collections.nCopies(references.size(), "?")) + ")";
        try (Connection connection = DriverManager.getConnection("jdbc:duckdb:")) {
            try (Statement statement = connection.createStatement()) {
                statement.execute("SET threads = " + Settings.integer("DUCKDB_THREADS", 4));
                statement.execute("SET enable_external_file_cache = "
                        + Settings.flag("DUCKDB_EXTERNAL_FILE_CACHE", false));
            }
            try (PreparedStatement statement = connection.prepareStatement(sql)) {
                statement.setString(1, archive.toString());
                int parameter = 2;
                for (String reference : references) {
                    statement.setString(parameter++, reference);
                }
                List<Row> rows = new ArrayList<>();
                try (ResultSet resultSet = statement.executeQuery()) {
                    int textColumns = resultSet.getMetaData().getColumnCount() - FIXED_COLUMNS;
                    while (resultSet.next()) {
                        String[] texts = new String[textColumns];
                        for (int i = 0; i < textColumns; i++) {
                            texts[i] = resultSet.getString(FIXED_COLUMNS + 1 + i);
                        }
                        rows.add(new Row(
                                resultSet.getLong(1),
                                resultSet.getString(2),
                                resultSet.getObject(3, LocalDateTime.class),
                                texts));
                    }
                }
                return rows;
            }
        }
    }
}
