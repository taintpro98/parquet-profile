package com.mbbank.archive.archive;

import com.github.luben.zstd.Zstd;
import com.mbbank.archive.common.Archive;
import com.mbbank.archive.common.Bytes;
import com.mbbank.archive.common.Row;
import com.mbbank.archive.common.RowCursor;
import com.mbbank.archive.common.Settings;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
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
import java.util.Set;
import java.util.TreeMap;

/**
 * Lưu theo dòng: các dòng (đã sắp theo reference) gom thành block, mỗi block nén ZSTD riêng; cuối
 * file có index thưa ghi reference đầu tiên và vị trí của từng block. FIND tìm nhị phân trong index
 * rồi chỉ giải nén đúng block chứa key. Định dạng giống hệt bản Go (xem README).
 *
 * <p>Settings: BLOCK_BYTES (1048576, kích thước block trước khi nén), BLOCK_ZSTD_LEVEL (3).
 */
public final class RowblockZstd implements Archive {

    private static final byte[] MAGIC = "ARCHBLK1".getBytes(StandardCharsets.US_ASCII);

    private record Block(String first, long offset, int size, int rawSize) {}

    @Override
    public String extension() {
        return "rbk";
    }

    // ---- WRITE ----

    @Override
    public long dump(RowCursor rows, Path out) throws Exception {
        int limit = Settings.integer("BLOCK_BYTES", 1 << 20);
        int level = Settings.integer("BLOCK_ZSTD_LEVEL", 3);
        List<Block> index = new ArrayList<>();
        Bytes block = new Bytes(limit + (64 << 10));
        byte[] compressed = new byte[0];
        String first = null;
        long offset = 0;
        long count = 0;
        try (FileChannel file = FileChannel.open(out, StandardOpenOption.CREATE, StandardOpenOption.WRITE,
                StandardOpenOption.TRUNCATE_EXISTING)) {
            while (true) {
                boolean more = rows.next();
                if (more) {
                    if (block.size() == 0) {
                        first = rows.reference();
                    }
                    block.putLong(rows.id());
                    block.putLong(toEpochMicros(rows.createdAt()));
                    block.putString(rows.reference());
                    for (int c = 1; c <= rows.textColumns(); c++) {
                        String text = rows.text(c);
                        if (text == null) {
                            block.putUvarint(0);
                        } else {
                            byte[] utf8 = text.getBytes(StandardCharsets.UTF_8);
                            block.putUvarint(utf8.length + 1L);
                            block.put(utf8);
                        }
                    }
                    count++;
                }
                if (block.size() > 0 && (!more || block.size() >= limit)) {
                    int bound = (int) Zstd.compressBound(block.size());
                    if (compressed.length < bound) {
                        compressed = new byte[bound];
                    }
                    long size = Zstd.compressByteArray(compressed, 0, bound, block.array(), 0, block.size(), level);
                    if (Zstd.isError(size)) {
                        throw new IOException("ZSTD: " + Zstd.getErrorName(size));
                    }
                    write(file, ByteBuffer.wrap(compressed, 0, (int) size));
                    index.add(new Block(first, offset, (int) size, block.size()));
                    offset += size;
                    block.clear();
                }
                if (!more) {
                    break;
                }
            }

            // index: số block, số cột text, rồi mỗi block: reference đầu, offset, kích thước nén, gốc
            Bytes idx = new Bytes(index.size() * 40 + 64);
            idx.putUvarint(index.size());
            idx.putUvarint(rows.textColumns());
            for (Block b : index) {
                idx.putString(b.first());
                idx.putUvarint(b.offset());
                idx.putUvarint(b.size());
                idx.putUvarint(b.rawSize());
            }
            ByteBuffer trailer = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN).putLong(offset).put(MAGIC);
            write(file, ByteBuffer.wrap(idx.array(), 0, idx.size()));
            write(file, trailer.flip());
        }
        return count;
    }

    private static void write(FileChannel file, ByteBuffer buffer) throws IOException {
        while (buffer.hasRemaining()) {
            file.write(buffer);
        }
    }

    private static long toEpochMicros(LocalDateTime value) {
        return value.toEpochSecond(ZoneOffset.UTC) * 1_000_000L + value.getNano() / 1_000L;
    }

    // ---- FIND ----

    @Override
    public List<Row> find(Path archive, Collection<String> references) throws Exception {
        try (FileChannel file = FileChannel.open(archive, StandardOpenOption.READ)) {
            ByteBuffer trailer = ByteBuffer.wrap(Bytes.read(file, file.size() - 16, 16)).order(ByteOrder.LITTLE_ENDIAN);
            long indexStart = trailer.getLong();
            if (!Arrays.equals(Arrays.copyOfRange(trailer.array(), 8, 16), MAGIC)) {
                throw new IOException("Not a rowblock file: " + archive);
            }
            int indexLength = (int) (file.size() - 16 - indexStart);
            Bytes idx = Bytes.wrap(Bytes.read(file, indexStart, indexLength), indexLength);
            int blockCount = (int) idx.getUvarint();
            int textColumns = (int) idx.getUvarint();
            Block[] index = new Block[blockCount];
            String[] firsts = new String[blockCount];
            for (int i = 0; i < blockCount; i++) {
                index[i] = new Block(idx.getString(), idx.getUvarint(), (int) idx.getUvarint(), (int) idx.getUvarint());
                firsts[i] = index[i].first();
            }

            // Mỗi key thuộc block cuối cùng có reference đầu <= key.
            Map<Integer, Set<String>> wanted = new TreeMap<>();
            for (String reference : references) {
                int found = Arrays.binarySearch(firsts, reference);
                int b = found >= 0 ? found : -found - 2;
                if (b >= 0) {
                    wanted.computeIfAbsent(b, key -> new HashSet<>()).add(reference);
                }
            }

            List<Row> rows = new ArrayList<>();
            byte[] raw = new byte[0];
            for (Map.Entry<Integer, Set<String>> entry : wanted.entrySet()) {
                Block block = index[entry.getKey()];
                byte[] compressed = Bytes.read(file, block.offset(), block.size());
                if (raw.length < block.rawSize()) {
                    raw = new byte[block.rawSize()];
                }
                long size = Zstd.decompressByteArray(raw, 0, block.rawSize(), compressed, 0, block.size());
                if (Zstd.isError(size)) {
                    throw new IOException("ZSTD: " + Zstd.getErrorName(size));
                }
                scan(Bytes.wrap(raw, block.rawSize()), textColumns, entry.getValue(), rows);
            }
            return rows;
        }
    }

    /** Đọc tuần tự các dòng trong block; chỉ dựng Row cho dòng có key cần tìm. */
    private static void scan(Bytes block, int textColumns, Set<String> wanted, List<Row> rows) {
        while (block.hasRemaining()) {
            long id = block.getLong();
            long micros = block.getLong();
            String reference = block.getString();
            boolean match = wanted.contains(reference);
            String[] texts = match ? new String[textColumns] : null;
            for (int c = 0; c < textColumns; c++) {
                int length = (int) block.getUvarint();
                if (length > 0) {
                    if (match) {
                        texts[c] = block.getString(length - 1);
                    } else {
                        block.skip(length - 1);
                    }
                }
            }
            if (match) {
                LocalDateTime createdAt = LocalDateTime.ofEpochSecond(
                        Math.floorDiv(micros, 1_000_000L), (int) Math.floorMod(micros, 1_000_000L) * 1_000, ZoneOffset.UTC);
                rows.add(new Row(id, reference, createdAt, texts));
            }
        }
    }
}
