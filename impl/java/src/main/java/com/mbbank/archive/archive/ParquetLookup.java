package com.mbbank.archive.archive;

import com.mbbank.archive.common.Archive;
import com.mbbank.archive.common.Row;
import com.mbbank.archive.common.RowCursor;
import com.mbbank.archive.common.Settings;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.apache.parquet.example.data.Group;
import org.apache.parquet.filter2.compat.FilterCompat;
import org.apache.parquet.filter2.predicate.FilterApi;
import org.apache.parquet.filter2.predicate.FilterPredicate;
import org.apache.parquet.hadoop.ParquetReader;
import org.apache.parquet.hadoop.api.ReadSupport;
import org.apache.parquet.hadoop.example.GroupReadSupport;
import org.apache.parquet.io.InputFile;
import org.apache.parquet.io.LocalInputFile;
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
 * Parquet chuẩn, chỉnh để tra theo key, đọc thẳng bằng parquet-java (không DuckDB).
 *
 * <p>Ghi như parquet-duckdb (row group 128 MB, dictionary mặc định, ZSTD), thêm hai tính năng chuẩn của
 * Parquet: bloom filter trên cột {@code reference} và page 64 KB để page index (column index + offset
 * index, parquet-java ghi mặc định) chia file thành đơn vị nhỏ. Đọc bằng {@code FilterApi.in} với
 * bloom filter và column index bật: parquet-java bỏ qua row group không chứa key, chỉ giải nén các page
 * chứa key, rồi lọc từng dòng.
 *
 * <p>Settings: PARQUET_COMPRESSION (ZSTD), PARQUET_ROW_GROUP_BYTES (128 MiB), PARQUET_PAGE_BYTES (64 KiB).
 */
public final class ParquetLookup implements Archive {

    private static final int FIXED_COLUMNS = 3;

    @Override
    public String name() {
        return "parquet-lookup";
    }

    @Override
    public String extension() {
        return "parquet";
    }

    // ---- WRITE ----

    @Override
    public long dump(RowCursor rows, Path out) throws Exception {
        MessageType schema = schema(rows.textColumns());
        long count = 0;
        // Row group theo mặc định của parquet-java: 128 MB tính theo byte, không giới hạn số dòng.
        // PARQUET_ROW_GROUP_ROWS / PARQUET_ROW_GROUP_BYTES chỉ để thử cấu hình khác (và cho test).
        Builder builder = new Builder(out, schema)
                .withCompressionCodec(CompressionCodecName.fromConf(Settings.string("PARQUET_COMPRESSION", "ZSTD")))
                .withRowGroupSize(Settings.number("PARQUET_ROW_GROUP_BYTES", ParquetWriter.DEFAULT_BLOCK_SIZE))
                .withWriteMode(ParquetFileWriter.Mode.OVERWRITE)
                .withPageSize(Settings.integer("PARQUET_PAGE_BYTES", 64 << 10))
                .withBloomFilterEnabled("reference", true);
        if (System.getenv("PARQUET_ROW_GROUP_ROWS") != null) {
            builder.withRowGroupRowCountLimit(Settings.integer("PARQUET_ROW_GROUP_ROWS", 0));
        }
        try (ParquetWriter<RowCursor> writer = builder.build()) {
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
        Set<Binary> keys = new HashSet<>();
        for (String reference : references) {
            keys.add(Binary.fromString(reference));
        }
        FilterPredicate predicate = FilterApi.in(FilterApi.binaryColumn("reference"), keys);
        List<Row> rows = new ArrayList<>();
        try (ParquetReader<Group> reader = new GroupReader(new LocalInputFile(archive))
                .withFilter(FilterCompat.get(predicate))
                .useStatsFilter(true)
                .useBloomFilter(true)
                .useColumnIndexFilter(true)
                .useRecordFilter(true)
                .build()) {
            for (Group group = reader.read(); group != null; group = reader.read()) {
                rows.add(toRow(group));
            }
        }
        return rows;
    }

    private static Row toRow(Group group) {
        int textColumns = group.getType().getFieldCount() - FIXED_COLUMNS;
        String[] texts = new String[textColumns];
        for (int i = 0; i < textColumns; i++) {
            if (group.getFieldRepetitionCount(FIXED_COLUMNS + i) > 0) {
                texts[i] = group.getString(FIXED_COLUMNS + i, 0);
            }
        }
        long micros = group.getLong("created_at", 0);
        LocalDateTime createdAt = LocalDateTime.ofEpochSecond(
                Math.floorDiv(micros, 1_000_000L), (int) Math.floorMod(micros, 1_000_000L) * 1_000, ZoneOffset.UTC);
        return new Row(group.getLong("id", 0), group.getString("reference", 0), createdAt, texts);
    }

    private static final class GroupReader extends ParquetReader.Builder<Group> {

        GroupReader(InputFile file) {
            super(file);
        }

        @Override
        protected ReadSupport<Group> getReadSupport() {
            return new GroupReadSupport();
        }
    }
}
