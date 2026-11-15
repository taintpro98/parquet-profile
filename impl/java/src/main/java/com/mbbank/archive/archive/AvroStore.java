package com.mbbank.archive.archive;

import com.mbbank.archive.common.Archive;
import com.mbbank.archive.common.Row;
import com.mbbank.archive.common.RowCursor;
import com.mbbank.archive.common.Settings;
import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import org.apache.avro.LogicalTypes;
import org.apache.avro.Schema;
import org.apache.avro.SchemaBuilder;
import org.apache.avro.file.CodecFactory;
import org.apache.avro.file.DataFileConstants;
import org.apache.avro.file.DataFileReader;
import org.apache.avro.file.DataFileWriter;
import org.apache.avro.generic.GenericData;
import org.apache.avro.generic.GenericDatumReader;
import org.apache.avro.generic.GenericDatumWriter;
import org.apache.avro.generic.GenericRecord;

/**
 * Avro object container file (thư viện avro chính thức), nén ZSTD theo block, không có index nào thêm.
 *
 * <p>Ghi: các dòng đã sắp theo reference, block theo mặc định của Avro (64 KB trước khi nén). Block lớn
 * nén tốt hơn một chút nhưng làm tra chậm hẳn: {@code sync(pos)} của Avro dò sync marker từng byte,
 * nên mỗi lần dò quét gần nửa block nén (đo được: block 1 MB chậm hơn 64 KB khoảng 12 lần, file chỉ
 * nhỏ hơn ~7%). Mọi công cụ Avro đọc được file (avro-tools, DuckDB read_avro, Spark).
 *
 * <p>Đọc: tìm nhị phân trên vị trí byte bằng API chuẩn {@code DataFileReader.sync(pos)}: nhảy tới
 * block kế tiếp, đọc reference đầu block, thu hẹp khoảng cho tới khi còn đúng block có thể chứa key,
 * rồi đọc tuần tự trong block đó. Mỗi key tốn khoảng log2(số block) lần giải nén một block; các lần
 * dò được nhớ lại để key sau dùng chung.
 *
 * <p>Settings: AVRO_BLOCK_BYTES (65536, mặc định của Avro), AVRO_ZSTD_LEVEL (3).
 */
public final class AvroStore implements Archive {

    @Override
    public String name() {
        return "avro";
    }

    @Override
    public String extension() {
        return "avro";
    }

    private static Schema schema(int textColumns) {
        SchemaBuilder.FieldAssembler<Schema> fields = SchemaBuilder.record("archive").fields()
                .requiredLong("id")
                .requiredString("reference")
                .name("created_at").type(LogicalTypes.localTimestampMicros().addToSchema(Schema.create(Schema.Type.LONG)))
                .noDefault();
        for (int i = 1; i <= textColumns; i++) {
            fields = fields.optionalString(String.format("text_%02d", i));
        }
        return fields.endRecord();
    }

    // ---- WRITE ----

    @Override
    public long dump(RowCursor rows, Path out) throws Exception {
        Schema schema = schema(rows.textColumns());
        long count = 0;
        try (DataFileWriter<GenericRecord> writer = new DataFileWriter<>(new GenericDatumWriter<GenericRecord>(schema))) {
            writer.setCodec(CodecFactory.zstandardCodec(Settings.integer("AVRO_ZSTD_LEVEL", 3)));
            writer.setSyncInterval(Settings.integer("AVRO_BLOCK_BYTES", DataFileConstants.DEFAULT_SYNC_INTERVAL));
            writer.create(schema, out.toFile());
            GenericRecord record = new GenericData.Record(schema);
            while (rows.next()) {
                record.put(0, rows.id());
                record.put(1, rows.reference());
                record.put(2, toEpochMicros(rows.createdAt()));
                for (int c = 1; c <= rows.textColumns(); c++) {
                    record.put(2 + c, rows.text(c));
                }
                writer.append(record); // ghi ngay vào buffer của block, dùng lại được record
                count++;
            }
        }
        return count;
    }

    private static long toEpochMicros(LocalDateTime value) {
        return value.toEpochSecond(ZoneOffset.UTC) * 1_000_000L + value.getNano() / 1_000L;
    }

    // ---- FIND ----

    /** Block bắt đầu tại start, reference đầu tiên là first. */
    private record Probe(long start, String first) {}

    @Override
    public List<Row> find(Path archive, Collection<String> references) throws Exception {
        File file = archive.toFile();
        long length = Files.size(archive);
        List<Row> rows = new ArrayList<>();
        try (DataFileReader<GenericRecord> reader = new DataFileReader<>(file, new GenericDatumReader<>())) {
            Map<Long, Probe> probes = new HashMap<>(); // đầu block → reference đầu, dùng chung giữa các key
            for (String key : new TreeSet<>(references)) {
                Probe block = locate(reader, length, key, probes);
                if (block == null) {
                    continue;
                }
                reader.seek(block.start());
                while (reader.hasNext()) {
                    GenericRecord record = reader.next();
                    int cmp = record.get(1).toString().compareTo(key);
                    if (cmp == 0) {
                        rows.add(toRow(record));
                    }
                    if (cmp >= 0 || reader.previousSync() != block.start()) {
                        break; // qua key, hoặc đã sang block sau
                    }
                }
            }
        }
        return rows;
    }

    /** Block cuối cùng có reference đầu tiên <= key, tìm bằng sync(pos); null nếu key nhỏ hơn mọi block. */
    private static Probe locate(DataFileReader<GenericRecord> reader, long length, String key, Map<Long, Probe> probes)
            throws Exception {
        Probe first = probe(reader, 0, probes);
        if (first == null || first.first().compareTo(key) > 0) {
            return null;
        }
        Probe best = first;
        long lo = first.start() + 1, hi = length;
        while (lo < hi) {
            long mid = lo + (hi - lo) / 2;
            Probe p = probe(reader, mid, probes);
            if (p == null || p.first().compareTo(key) > 0) {
                hi = mid; // block tại/ sau mid đã vượt key
            } else {
                best = p;
                lo = p.start() + 1; // có block bắt đầu <= key ở p.start(); tìm tiếp phía sau
            }
        }
        return best;
    }

    /**
     * Block bắt đầu tại/ sau position. sync() chỉ dò tới sync marker kế tiếp; previousSync() cho vị trí
     * đầu block mà chưa cần giải nén, nên mỗi block chỉ giải nén một lần dù nhiều vị trí dò rơi vào nó.
     */
    private static Probe probe(DataFileReader<GenericRecord> reader, long position, Map<Long, Probe> blocks)
            throws Exception {
        reader.sync(position);
        long start = reader.previousSync();
        if (blocks.containsKey(start)) {
            return blocks.get(start);
        }
        Probe p = reader.hasNext() ? new Probe(start, reader.next().get(1).toString()) : null;
        blocks.put(start, p);
        return p;
    }

    private static Row toRow(GenericRecord record) {
        int textColumns = record.getSchema().getFields().size() - 3;
        String[] texts = new String[textColumns];
        for (int i = 0; i < textColumns; i++) {
            Object value = record.get(3 + i);
            texts[i] = value == null ? null : value.toString();
        }
        long micros = (Long) record.get(2);
        LocalDateTime createdAt = LocalDateTime.ofEpochSecond(
                Math.floorDiv(micros, 1_000_000L), (int) Math.floorMod(micros, 1_000_000L) * 1_000, ZoneOffset.UTC);
        return new Row((Long) record.get(0), record.get(1).toString(), createdAt, texts);
    }
}
