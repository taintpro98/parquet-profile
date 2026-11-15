package com.mbbank.archive.archive;

import com.mbbank.archive.common.Archive;
import com.mbbank.archive.common.Row;
import com.mbbank.archive.common.RowCursor;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.hive.ql.exec.vector.BytesColumnVector;
import org.apache.hadoop.hive.ql.exec.vector.LongColumnVector;
import org.apache.hadoop.hive.ql.exec.vector.TimestampColumnVector;
import org.apache.hadoop.hive.ql.exec.vector.VectorizedRowBatch;
import org.apache.hadoop.hive.ql.io.sarg.PredicateLeaf;
import org.apache.hadoop.hive.ql.io.sarg.SearchArgument;
import org.apache.hadoop.hive.ql.io.sarg.SearchArgumentFactory;
import org.apache.orc.CompressionKind;
import org.apache.orc.OrcFile;
import org.apache.orc.Reader;
import org.apache.orc.RecordReader;
import org.apache.orc.TypeDescription;
import org.apache.orc.Writer;

/**
 * Apache ORC (orc-core, thư viện chính thức), nén ZSTD.
 *
 * <p>Ghi: row index mỗi 10.000 dòng (mặc định của ORC) và bloom filter trên cột {@code reference}, cả
 * hai là tính năng chuẩn của ORC; mỗi lô dump 10.000 dòng là một VectorizedRowBatch. Timestamp lưu theo
 * UTC để múi giờ của JVM không làm lệch giá trị.
 *
 * <p>Đọc: {@code SearchArgument} với {@code IN} trên reference. ORC dùng min/max và bloom filter của từng
 * đoạn 10.000 dòng để bỏ qua đoạn không chứa key, chỉ giải nén các đoạn còn lại, rồi lọc từng dòng.
 */
public final class OrcStore implements Archive {

    private static final int BATCH = 10_000;

    @Override
    public String name() {
        return "orc";
    }

    @Override
    public String extension() {
        return "orc";
    }

    private static TypeDescription schema(int textColumns) {
        TypeDescription schema = TypeDescription.createStruct()
                .addField("id", TypeDescription.createLong())
                .addField("reference", TypeDescription.createString())
                .addField("created_at", TypeDescription.createTimestamp());
        for (int i = 1; i <= textColumns; i++) {
            schema.addField(String.format("text_%02d", i), TypeDescription.createString());
        }
        return schema;
    }

    // ---- WRITE ----

    @Override
    public long dump(RowCursor rows, Path out) throws Exception {
        TypeDescription schema = schema(rows.textColumns());
        Configuration conf = new Configuration();
        long count = 0;
        try (Writer writer = OrcFile.createWriter(new org.apache.hadoop.fs.Path(out.toUri()),
                OrcFile.writerOptions(conf)
                        .setSchema(schema)
                        .compress(CompressionKind.ZSTD)
                        .bloomFilterColumns("reference")
                        .useUTCTimestamp(true)
                        .overwrite(true))) {
            VectorizedRowBatch batch = schema.createRowBatch(BATCH);
            LongColumnVector ids = (LongColumnVector) batch.cols[0];
            BytesColumnVector refs = (BytesColumnVector) batch.cols[1];
            TimestampColumnVector times = (TimestampColumnVector) batch.cols[2];
            while (rows.next()) {
                int r = batch.size++;
                ids.vector[r] = rows.id();
                refs.setVal(r, rows.reference().getBytes(StandardCharsets.UTF_8));
                LocalDateTime at = rows.createdAt();
                times.time[r] = at.toEpochSecond(ZoneOffset.UTC) * 1000 + at.getNano() / 1_000_000;
                times.nanos[r] = at.getNano();
                for (int c = 1; c <= rows.textColumns(); c++) {
                    BytesColumnVector col = (BytesColumnVector) batch.cols[2 + c];
                    String text = rows.text(c);
                    if (text == null) {
                        col.noNulls = false;
                        col.isNull[r] = true;
                    } else {
                        col.setVal(r, text.getBytes(StandardCharsets.UTF_8));
                    }
                }
                count++;
                if (batch.size == BATCH) {
                    writer.addRowBatch(batch);
                    batch.reset();
                }
            }
            if (batch.size > 0) {
                writer.addRowBatch(batch);
            }
        }
        return count;
    }

    // ---- FIND ----

    @Override
    public List<Row> find(Path archive, Collection<String> references) throws Exception {
        Set<String> wanted = new HashSet<>(references);
        SearchArgument sarg = SearchArgumentFactory.newBuilder()
                .in("reference", PredicateLeaf.Type.STRING, references.toArray())
                .build();
        List<Row> rows = new ArrayList<>();
        Configuration conf = new Configuration();
        try (Reader reader = OrcFile.createReader(new org.apache.hadoop.fs.Path(archive.toUri()),
                        OrcFile.readerOptions(conf).useUTCTimestamp(true));
                RecordReader records = reader.rows(reader.options()
                        .searchArgument(sarg, new String[] {"reference"})
                        .useSelected(false))) {
            VectorizedRowBatch batch = reader.getSchema().createRowBatch(BATCH);
            int textColumns = batch.cols.length - 3;
            while (records.nextBatch(batch)) {
                LongColumnVector ids = (LongColumnVector) batch.cols[0];
                BytesColumnVector refs = (BytesColumnVector) batch.cols[1];
                TimestampColumnVector times = (TimestampColumnVector) batch.cols[2];
                for (int r = 0; r < batch.size; r++) {
                    String reference = refs.toString(r);
                    if (!wanted.contains(reference)) {
                        continue;
                    }
                    String[] texts = new String[textColumns];
                    for (int c = 0; c < textColumns; c++) {
                        BytesColumnVector col = (BytesColumnVector) batch.cols[3 + c];
                        int i = col.isRepeating ? 0 : r;
                        if (col.noNulls || !col.isNull[i]) {
                            texts[c] = col.toString(i);
                        }
                    }
                    LocalDateTime createdAt = LocalDateTime.ofEpochSecond(
                            Math.floorDiv(times.time[r], 1000L), times.nanos[r], ZoneOffset.UTC);
                    rows.add(new Row(ids.vector[r], reference, createdAt, texts));
                }
            }
        }
        return rows;
    }
}
