package com.mbbank.profile.parquet;

import com.mbbank.profile.config.DatabaseConfig;
import java.io.IOException;
import java.nio.file.Path;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
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
 * Writes the current ResultSet row straight into Parquet. The ResultSet itself is the "record"
 * type of the ParquetWriter, so no intermediate row object exists between JDBC and Parquet.
 */
public final class ParquetArchiveWriter implements AutoCloseable {

    private static final int FIXED_COLUMNS = 3;

    private final ParquetWriter<ResultSet> writer;

    public ParquetArchiveWriter(Path file, DatabaseConfig config) throws IOException {
        this.writer = new Builder(file, schema(config.oracleTextColumns()))
                .withCompressionCodec(CompressionCodecName.fromConf(config.parquetCompression()))
                .withRowGroupRowCountLimit(config.parquetRowGroupRows())
                .withRowGroupSize(config.parquetRowGroupBytes())
                .withWriteMode(ParquetFileWriter.Mode.OVERWRITE)
                .build();
    }

    public void write(ResultSet resultSet) throws IOException {
        writer.write(resultSet);
    }

    @Override
    public void close() throws IOException {
        writer.close();
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
        return builder.named("archive_profile_data");
    }

    private static final class Builder extends ParquetWriter.Builder<ResultSet, Builder> {

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
        protected WriteSupport<ResultSet> getWriteSupport(Configuration conf) {
            return new ResultSetWriteSupport(schema);
        }
    }

    private static final class ResultSetWriteSupport extends WriteSupport<ResultSet> {

        private final MessageType schema;
        private RecordConsumer consumer;

        ResultSetWriteSupport(MessageType schema) {
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
        public void write(ResultSet row) {
            try {
                consumer.startMessage();

                consumer.startField("id", 0);
                consumer.addLong(row.getLong(1));
                consumer.endField("id", 0);

                consumer.startField("reference", 1);
                consumer.addBinary(Binary.fromString(row.getString(2)));
                consumer.endField("reference", 1);

                consumer.startField("created_at", 2);
                consumer.addLong(toEpochMicros(row.getObject(3, LocalDateTime.class)));
                consumer.endField("created_at", 2);

                int fieldCount = schema.getFieldCount();
                for (int field = FIXED_COLUMNS; field < fieldCount; field++) {
                    String value = row.getString(field + 1);
                    if (value != null) {
                        String name = schema.getFieldName(field);
                        consumer.startField(name, field);
                        consumer.addBinary(Binary.fromString(value));
                        consumer.endField(name, field);
                    }
                }

                consumer.endMessage();
            } catch (SQLException e) {
                throw new IllegalStateException("Failed to read the current Oracle row", e);
            }
        }

        /** Oracle TIMESTAMP has no time zone, so the wall-clock value is stored as is. */
        private static long toEpochMicros(LocalDateTime value) {
            return value.toEpochSecond(ZoneOffset.UTC) * 1_000_000L + value.getNano() / 1_000L;
        }
    }
}
