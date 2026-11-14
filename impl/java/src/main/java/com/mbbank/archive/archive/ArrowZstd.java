package com.mbbank.archive.archive;

import com.mbbank.archive.common.Archive;
import com.mbbank.archive.common.Bytes;
import com.mbbank.archive.common.Row;
import com.mbbank.archive.common.RowCursor;
import com.mbbank.archive.common.Settings;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
import java.nio.channels.SeekableByteChannel;
import java.nio.channels.WritableByteChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;
import org.apache.arrow.compression.CommonsCompressionFactory;
import org.apache.arrow.memory.BufferAllocator;
import org.apache.arrow.memory.RootAllocator;
import org.apache.arrow.vector.BigIntVector;
import org.apache.arrow.vector.TimeStampMicroVector;
import org.apache.arrow.vector.VarCharVector;
import org.apache.arrow.vector.VectorSchemaRoot;
import org.apache.arrow.vector.compression.CompressionUtil;
import org.apache.arrow.vector.ipc.ArrowFileReader;
import org.apache.arrow.vector.ipc.ArrowFileWriter;
import org.apache.arrow.vector.ipc.message.ArrowBlock;
import org.apache.arrow.vector.ipc.message.IpcOption;
import org.apache.arrow.vector.types.TimeUnit;
import org.apache.arrow.vector.types.pojo.ArrowType;
import org.apache.arrow.vector.types.pojo.Field;
import org.apache.arrow.vector.types.pojo.FieldType;
import org.apache.arrow.vector.types.pojo.Schema;

/**
 * Lưu theo cột bằng Apache Arrow IPC (file format), mỗi buffer nén ZSTD. Các dòng (đã sắp theo
 * reference) chia thành lô ARROW_BATCH_ROWS dòng. Arrow không có thống kê min/max, nên sau phần Arrow
 * là một index nhỏ ghi reference đầu tiên của từng lô; FIND tìm nhị phân trong index rồi chỉ giải
 * nén lô chứa key. Định dạng giống hệt bản Go (xem README).
 *
 * <p>Settings: ARROW_BATCH_ROWS (1024).
 */
public final class ArrowZstd implements Archive {

    private static final byte[] MAGIC = "ARCHARW1".getBytes(StandardCharsets.US_ASCII);

    @Override
    public String extension() {
        return "arrow";
    }

    private static Schema schema(int textColumns) {
        List<Field> fields = new ArrayList<>();
        fields.add(new Field("id", FieldType.notNullable(new ArrowType.Int(64, true)), null));
        fields.add(new Field("reference", FieldType.notNullable(ArrowType.Utf8.INSTANCE), null));
        fields.add(new Field("created_at", FieldType.notNullable(new ArrowType.Timestamp(TimeUnit.MICROSECOND, null)), null));
        for (int i = 1; i <= textColumns; i++) {
            fields.add(new Field(String.format("text_%02d", i), FieldType.nullable(ArrowType.Utf8.INSTANCE), null));
        }
        return new Schema(fields);
    }

    // ---- WRITE ----

    @Override
    public long dump(RowCursor rows, Path out) throws Exception {
        int batchRows = Settings.integer("ARROW_BATCH_ROWS", 1024);
        List<String> firsts = new ArrayList<>();
        long count = 0;
        try (BufferAllocator allocator = new RootAllocator();
                VectorSchemaRoot root = VectorSchemaRoot.create(schema(rows.textColumns()), allocator);
                FileChannel file = FileChannel.open(out, StandardOpenOption.CREATE, StandardOpenOption.WRITE,
                        StandardOpenOption.TRUNCATE_EXISTING)) {
            BigIntVector ids = (BigIntVector) root.getVector(0);
            VarCharVector refs = (VarCharVector) root.getVector(1);
            TimeStampMicroVector times = (TimeStampMicroVector) root.getVector(2);
            VarCharVector[] texts = new VarCharVector[rows.textColumns()];
            for (int i = 0; i < texts.length; i++) {
                texts[i] = (VarCharVector) root.getVector(3 + i);
            }
            // ArrowFileWriter đóng kênh khi đóng writer, nhưng index còn phải ghi sau phần Arrow.
            WritableByteChannel unclosable = new WritableByteChannel() {
                @Override
                public int write(ByteBuffer src) throws IOException {
                    return file.write(src);
                }

                @Override
                public boolean isOpen() {
                    return file.isOpen();
                }

                @Override
                public void close() {}
            };
            try (ArrowFileWriter writer = new ArrowFileWriter(root, null, unclosable, Map.of(), IpcOption.DEFAULT,
                    CommonsCompressionFactory.INSTANCE, CompressionUtil.CodecType.ZSTD, Optional.empty())) {
                writer.start();
                int inBatch = 0;
                root.allocateNew();
                while (true) {
                    boolean more = rows.next();
                    if (more) {
                        String reference = rows.reference();
                        if (inBatch == 0) {
                            firsts.add(reference);
                        }
                        ids.setSafe(inBatch, rows.id());
                        refs.setSafe(inBatch, reference.getBytes(StandardCharsets.UTF_8));
                        times.setSafe(inBatch, toEpochMicros(rows.createdAt()));
                        for (int c = 0; c < texts.length; c++) {
                            String text = rows.text(c + 1);
                            if (text == null) {
                                texts[c].setNull(inBatch);
                            } else {
                                texts[c].setSafe(inBatch, text.getBytes(StandardCharsets.UTF_8));
                            }
                        }
                        inBatch++;
                        count++;
                    }
                    if (inBatch > 0 && (!more || inBatch == batchRows)) {
                        root.setRowCount(inBatch);
                        writer.writeBatch();
                        root.allocateNew();
                        inBatch = 0;
                    }
                    if (!more) {
                        break;
                    }
                }
                writer.end();
            }
            long arrowLength = file.position();
            Bytes idx = new Bytes(firsts.size() * 24 + 32);
            idx.putUvarint(firsts.size());
            for (String first : firsts) {
                idx.putString(first);
            }
            idx.putLong(arrowLength);
            idx.put(MAGIC);
            ByteBuffer buffer = ByteBuffer.wrap(idx.array(), 0, idx.size());
            while (buffer.hasRemaining()) {
                file.write(buffer);
            }
        }
        return count;
    }

    private static long toEpochMicros(LocalDateTime value) {
        return value.toEpochSecond(ZoneOffset.UTC) * 1_000_000L + value.getNano() / 1_000L;
    }

    // ---- FIND ----

    @Override
    public List<Row> find(Path archive, Collection<String> references) throws Exception {
        try (FileChannel file = FileChannel.open(archive, StandardOpenOption.READ);
                BufferAllocator allocator = new RootAllocator()) {
            ByteBuffer trailer = ByteBuffer.wrap(Bytes.read(file, file.size() - 16, 16)).order(ByteOrder.LITTLE_ENDIAN);
            long arrowLength = trailer.getLong();
            if (!Arrays.equals(Arrays.copyOfRange(trailer.array(), 8, 16), MAGIC)) {
                throw new IOException("Not an indexed arrow file: " + archive);
            }
            int indexLength = (int) (file.size() - 16 - arrowLength);
            Bytes idx = Bytes.wrap(Bytes.read(file, arrowLength, indexLength), indexLength);
            String[] firsts = new String[(int) idx.getUvarint()];
            for (int i = 0; i < firsts.length; i++) {
                firsts[i] = idx.getString();
            }

            // Mỗi key thuộc lô cuối cùng có reference đầu <= key.
            Map<Integer, Set<String>> wanted = new TreeMap<>();
            for (String reference : references) {
                int found = Arrays.binarySearch(firsts, reference);
                int b = found >= 0 ? found : -found - 2;
                if (b >= 0) {
                    wanted.computeIfAbsent(b, key -> new HashSet<>()).add(reference);
                }
            }

            List<Row> rows = new ArrayList<>();
            try (ArrowFileReader reader = new ArrowFileReader(
                    new Limited(file, arrowLength), allocator, CommonsCompressionFactory.INSTANCE)) {
                List<ArrowBlock> blocks = reader.getRecordBlocks();
                VectorSchemaRoot root = reader.getVectorSchemaRoot();
                int textColumns = root.getSchema().getFields().size() - 3;
                for (Map.Entry<Integer, Set<String>> entry : wanted.entrySet()) {
                    reader.loadRecordBatch(blocks.get(entry.getKey()));
                    BigIntVector ids = (BigIntVector) root.getVector(0);
                    VarCharVector refs = (VarCharVector) root.getVector(1);
                    TimeStampMicroVector times = (TimeStampMicroVector) root.getVector(2);
                    for (int i = 0; i < root.getRowCount(); i++) {
                        String reference = new String(refs.get(i), StandardCharsets.UTF_8);
                        if (!entry.getValue().contains(reference)) {
                            continue;
                        }
                        String[] texts = new String[textColumns];
                        for (int c = 0; c < textColumns; c++) {
                            VarCharVector text = (VarCharVector) root.getVector(3 + c);
                            if (!text.isNull(i)) {
                                texts[c] = new String(text.get(i), StandardCharsets.UTF_8);
                            }
                        }
                        long micros = times.get(i);
                        rows.add(new Row(ids.get(i), reference, LocalDateTime.ofEpochSecond(
                                Math.floorDiv(micros, 1_000_000L), (int) Math.floorMod(micros, 1_000_000L) * 1_000,
                                ZoneOffset.UTC), texts));
                    }
                }
            }
            return rows;
        }
    }

    /** Phần Arrow của file: Arrow đọc footer ở cuối kênh, nên kênh phải kết thúc trước index. */
    private static final class Limited implements SeekableByteChannel {

        private final FileChannel file;
        private final long size;
        private long position;

        Limited(FileChannel file, long size) {
            this.file = file;
            this.size = size;
        }

        @Override
        public int read(ByteBuffer dst) throws IOException {
            if (position >= size) {
                return -1;
            }
            int limit = dst.limit();
            dst.limit((int) Math.min(limit, dst.position() + size - position));
            int read = file.read(dst, position);
            dst.limit(limit);
            if (read > 0) {
                position += read;
            }
            return read;
        }

        @Override
        public int write(ByteBuffer src) {
            throw new UnsupportedOperationException();
        }

        @Override
        public long position() {
            return position;
        }

        @Override
        public SeekableByteChannel position(long newPosition) {
            position = newPosition;
            return this;
        }

        @Override
        public long size() {
            return size;
        }

        @Override
        public SeekableByteChannel truncate(long newSize) {
            throw new UnsupportedOperationException();
        }

        @Override
        public boolean isOpen() {
            return file.isOpen();
        }

        @Override
        public void close() {
            // the FileChannel is closed by find()
        }
    }
}
