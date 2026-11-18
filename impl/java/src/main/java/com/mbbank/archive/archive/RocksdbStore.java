package com.mbbank.archive.archive;

import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonToken;
import com.mbbank.archive.common.Archive;
import com.mbbank.archive.common.Jsonl;
import com.mbbank.archive.common.Lookup;
import com.mbbank.archive.common.Row;
import com.mbbank.archive.common.RowCursor;
import com.mbbank.archive.common.Settings;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Properties;
import org.rocksdb.BlockBasedTableConfig;
import org.rocksdb.BloomFilter;
import org.rocksdb.ColumnFamilyOptions;
import org.rocksdb.ConfigOptions;
import org.rocksdb.DBOptions;
import org.rocksdb.EnvOptions;
import org.rocksdb.IndexType;
import org.rocksdb.IngestExternalFileOptions;
import org.rocksdb.LRUCache;
import org.rocksdb.Options;
import org.rocksdb.RocksDB;
import org.rocksdb.SstFileWriter;

/**
 * RocksDB (rocksdbjni, thư viện chính thức): mỗi ngày là một DB, ghi một lần rồi chỉ đọc.
 *
 * <p>Ghi: các dòng đã sắp theo reference được ghi tuần tự bằng {@code SstFileWriter} ra một file SST
 * rồi nạp vào DB của ngày bằng {@code ingestExternalFile}, không qua WAL, memtable hay compaction.
 * Key là REFERENCE; value là bản ghi JSON theo định dạng chuẩn của repo, nên {@code ldb scan} đọc được.
 * Block nén ZSTD, bloom filter 10 bit/key, block 64 KB (mỗi dòng ~21 KB, block nhỏ hơn nén kém).
 *
 * <p>Đọc: mở DB chỉ đọc, {@code multiGetAsList}. Index và bloom filter nằm trong block cache có giới hạn
 * ROCKSDB_CACHE_BYTES, để RAM native không vượt mức đặt ra.
 *
 * <p>Settings: ROCKSDB_BLOCK_BYTES (65536), ROCKSDB_CACHE_BYTES (64 MiB), ROCKSDB_CACHE_SHARD_BITS (-1 = tự chọn),
 * ROCKSDB_INDEX (partitioned | binary), ROCKSDB_CACHE_INDEX (true), ROCKSDB_METADATA_BYTES (4096), ROCKSDB_BLOOM_BITS (10),
 * ROCKSDB_ZSTD_DICT_BYTES / _TRAIN_BYTES / _BUFFER_BYTES / _LEVEL (xem Config.compression).
 */
public final class RocksdbStore implements Archive {

    static {
        RocksDB.loadLibrary();
    }

    private static final JsonFactory JSON = new JsonFactory();

    @Override
    public String name() {
        return "rocksdb";
    }

    /** Một ngày là một thư mục DB: YYYY-MM-DD.rocksdb/. */
    @Override
    public String extension() {
        return "rocksdb";
    }

    /** Mọi đối tượng native được đóng cùng nhau; RocksDB không để JVM tự dọn chúng. */
    private static final class Config implements AutoCloseable {
        final LRUCache cache = new LRUCache(Settings.number("ROCKSDB_CACHE_BYTES", 64L << 20),
                Settings.integer("ROCKSDB_CACHE_SHARD_BITS", -1));
        final BloomFilter bloom = new BloomFilter(Settings.integer("ROCKSDB_BLOOM_BITS", 10));
        final DBOptions dbOptions = new DBOptions();
        final ColumnFamilyOptions cfOptions = compression();
        final Options options = new Options(dbOptions, cfOptions).setTableFormatConfig(table(bloom, cache));

        /** RocksDB kDefaultCompressionLevel: ZSTD tự chọn mức mặc định (3). */
        private static final int ZSTD_DEFAULT_LEVEL = 32767;

        /**
         * Nén ZSTD, giống setZstdOptions của bản Go. Dựng qua parser options native của RocksDB
         * ({@code getColumnFamilyOptionsFromProps}) vì {@code CompressionOptions} của rocksdbjni 11.8 không có
         * setter cho {@code max_dict_buffer_bytes}; parser native thì có. Parser chạy chế độ nghiêm ngặt: sai tên
         * tham số thì báo lỗi, không âm thầm chạy với mặc định. Đặt cho cả compression_opts lẫn bottommost vì
         * SstFileWriter dùng bottommost khi có. Không đặt biến môi trường thì chỉ chọn ZSTD, như cấu hình cũ.
         *
         * <p>ROCKSDB_ZSTD_DICT_BYTES (0 = tắt), ROCKSDB_ZSTD_TRAIN_BYTES (100 × dict), ROCKSDB_ZSTD_BUFFER_BYTES
         * (64 MiB; 0 của RocksDB là giữ cả file SST trong RAM để lấy mẫu), ROCKSDB_ZSTD_LEVEL (mặc định của ZSTD).
         */
        private static ColumnFamilyOptions compression() {
            int dict = Settings.integer("ROCKSDB_ZSTD_DICT_BYTES", 0);
            int level = Settings.integer("ROCKSDB_ZSTD_LEVEL", ZSTD_DEFAULT_LEVEL);
            Properties props = new Properties();
            props.setProperty("compression", "kZSTD");
            props.setProperty("bottommost_compression", "kZSTD");
            if (dict > 0 || level != ZSTD_DEFAULT_LEVEL) {
                long train = dict > 0 ? Settings.number("ROCKSDB_ZSTD_TRAIN_BYTES", dict * 100L) : 0;
                long buffer = dict > 0 ? Settings.number("ROCKSDB_ZSTD_BUFFER_BYTES", 64L << 20) : 0;
                String opts = "{level=" + level + ";max_dict_bytes=" + dict + ";zstd_max_train_bytes=" + train
                        + ";max_dict_buffer_bytes=" + buffer + ";use_zstd_dict_trainer=true;enabled=true;}";
                props.setProperty("compression_opts", opts);
                props.setProperty("bottommost_compression_opts", opts);
            }
            try (ConfigOptions parser = new ConfigOptions().setIgnoreUnknownOptions(false)
                    .setInputStringsEscaped(false)) {
                ColumnFamilyOptions cf = ColumnFamilyOptions.getColumnFamilyOptionsFromProps(parser, props);
                if (cf == null) {
                    throw new IllegalArgumentException("RocksDB không nhận cấu hình nén: " + props);
                }
                return cf;
            }
        }

        /**
         * ROCKSDB_INDEX=partitioned (mặc định): index và bloom chia mảnh
         * (kTwoLevelIndexSearch), mảnh cấp trên ghim trong cache, mỗi lần tra chỉ nạp một mảnh nhỏ. binary:
         * index một khối (mặc định của RocksDB); với dòng ~21 KB index lớn hơn một shard của cache nên bị nạp
         * lại ở mỗi key, thời gian tra tăng theo kích thước file (đo được: 9.6 s → 19.2 s cho 1000 key, 1M → 2M).
         * ROCKSDB_CACHE_INDEX=false: index/bloom giữ ngoài block cache, nạp một lần khi mở file.
         */
        private static BlockBasedTableConfig table(BloomFilter bloom, LRUCache cache) {
            BlockBasedTableConfig table = new BlockBasedTableConfig()
                    .setBlockSize(Settings.integer("ROCKSDB_BLOCK_BYTES", 64 << 10))
                    .setFilterPolicy(bloom)
                    .setCacheIndexAndFilterBlocks(Settings.flag("ROCKSDB_CACHE_INDEX", true))
                    .setCacheIndexAndFilterBlocksWithHighPriority(true)
                    .setPinL0FilterAndIndexBlocksInCache(true)
                    .setBlockCache(cache);
            if (Settings.string("ROCKSDB_INDEX", "partitioned").equals("partitioned")) {
                table.setIndexType(IndexType.kTwoLevelIndexSearch)
                        .setPartitionFilters(true)
                        .setMetadataBlockSize(Settings.integer("ROCKSDB_METADATA_BYTES", 4096))
                        .setPinTopLevelIndexAndFilter(true);
            }
            return table;
        }

        @Override
        public void close() {
            options.close();
            cfOptions.close();
            dbOptions.close();
            bloom.close();
            cache.close();
        }
    }

    // ---- WRITE ----

    @Override
    public long dump(RowCursor rows, Path out) throws Exception {
        Files.createDirectories(out);
        Path sst = out.resolveSibling(out.getFileName() + ".sst");
        long count = 0;
        StringBuilder value = new StringBuilder(32 << 10);
        try (Config config = new Config();
                EnvOptions env = new EnvOptions();
                SstFileWriter writer = new SstFileWriter(env, config.options)) {
            writer.open(sst.toString());
            while (rows.next()) {
                String[] texts = new String[rows.textColumns()];
                for (int i = 0; i < texts.length; i++) {
                    texts[i] = rows.text(i + 1);
                }
                Row row = new Row(rows.id(), rows.reference(), rows.createdAt(), texts);
                value.setLength(0);
                Jsonl.append(value, row);
                value.setLength(value.length() - 1); // bỏ xuống dòng
                writer.put(row.reference().getBytes(StandardCharsets.UTF_8),
                        value.toString().getBytes(StandardCharsets.UTF_8));
                count++;
            }
            writer.finish();
        }
        try (Config config = new Config();
                IngestExternalFileOptions ingest = new IngestExternalFileOptions().setMoveFiles(true)) {
            config.options.setCreateIfMissing(true);
            try (RocksDB db = RocksDB.open(config.options, out.toString())) {
                db.ingestExternalFile(List.of(sst.toString()), ingest);
            }
        } finally {
            Files.deleteIfExists(sst);
        }
        return count;
    }

    // ---- FIND ----

    @Override
    public List<Row> find(Path archive, Collection<String> references) throws Exception {
        List<byte[]> keys = new ArrayList<>(references.size());
        for (String reference : references) {
            keys.add(reference.getBytes(StandardCharsets.UTF_8));
        }
        List<Row> rows = new ArrayList<>();
        try (Config config = new Config(); RocksDB db = RocksDB.openReadOnly(config.options, archive.toString())) {
            for (byte[] value : db.multiGetAsList(keys)) {
                if (value != null) {
                    rows.add(parse(value));
                }
            }
        }
        return rows;
    }

    /** Đọc lại bản ghi JSON: id, reference, created_at rồi text_01..text_NN (null là NULL). */
    private static Row parse(byte[] value) throws Exception {
        long id = 0;
        String reference = null;
        LocalDateTime createdAt = null;
        List<String> texts = new ArrayList<>(70);
        try (JsonParser parser = JSON.createParser(value)) {
            parser.nextToken(); // {
            while (parser.nextToken() == JsonToken.FIELD_NAME) {
                String field = parser.currentName();
                JsonToken token = parser.nextToken();
                switch (field) {
                    case "id" -> id = parser.getLongValue();
                    case "reference" -> reference = parser.getText();
                    case "created_at" -> createdAt = LocalDateTime.parse(parser.getText(), Lookup.TIMESTAMP);
                    default -> texts.add(token == JsonToken.VALUE_NULL ? null : parser.getText());
                }
            }
        }
        return new Row(id, reference, createdAt, texts.toArray(String[]::new));
    }
}
