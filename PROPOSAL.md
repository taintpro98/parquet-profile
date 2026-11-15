# Phương án lưu trữ

Bài toán: key (`REFERENCE`) + timestamp (`CREATED_AT`) → một bản ghi đầy đủ. Mỗi ngày một file, ghi
một lần rồi chỉ đọc. Metric: xem `METRICS.md`.

Điều kiện chọn: đạt các metric định tính (stability, thân thiện với dev, prod ready) và chỉ dùng định
dạng, thư viện chuẩn. Ngôn ngữ không phải điều kiện: thư viện chỉ tốt ở Java thì chỉ bench Java.

## 5 phương án

#: 1
Phương án: Parquet + DuckDB (đã có)
Tra 1 key bằng gì: SQL WHERE
Stability: ✅ cao
Thân thiện dev: ✅ SQL
Prod ready: ✅
Bench bằng: Java, Go
────────────────────────────────────────
#: 2
Phương án: Parquet tra theo key
Tra 1 key bằng gì: page index + bloom filter, thư viện Parquet đọc thẳng
Stability: ✅ cao
Thân thiện dev: ✅ SQL, pyarrow
Prod ready: ✅
Bench bằng: Java, Go
────────────────────────────────────────
#: 3
Phương án: RocksDB
Tra 1 key bằng gì: db.get(key)
Stability: ✅ cao
Thân thiện dev: ⚠️ trung bình: không có SQL, value phải mã hoá bằng Protobuf/Avro
Prod ready: ✅ Kafka Streams, Flink
Bench bằng: Java, Go
────────────────────────────────────────
#: 4
Phương án: ORC (mới)
Tra 1 key bằng gì: row index mỗi 10k dòng + bloom filter, lọc bằng SearchArgument
Stability: ✅ cao (từ 2013, Hive/Spark/Trino)
Thân thiện dev: ✅ Spark, Trino, pyarrow
Prod ready: ✅
Bench bằng: Java (orc-core)
────────────────────────────────────────
#: 5
Phương án: Avro, tìm nhị phân qua sync marker (mới)
Tra 1 key bằng gì: DataFileReader.sync(pos) nhảy tới block gần nhất, tìm nhị phân theo key. Không cần index, chỉ dùng API
  chuẩn
Stability: ✅ rất cao
Thân thiện dev: ✅ avro-tools, DuckDB read_avro, Spark
Prod ready: ✅ Kafka, Hadoop
Bench bằng: Java (avro)

## Dự đoán trước khi đo

- Tra nhanh, ít RAM: RocksDB, rồi Parquet tra theo key và ORC.
- Bền lâu, thân thiện dev: Parquet, Avro, ORC.
- Avro chậm hơn khi tra (tìm nhị phân đọc khoảng 20 block mỗi key) nhưng đơn giản và bền nhất.

## Không chọn

| Phương án | Lý do |
| --- | --- |
| Rowblock (tự viết), Arrow + index nối thêm | Tự chế định dạng. Giữ số đã đo để tham khảo, không đo lại |
| SQLite | Không nén, file to bằng Oracle |
| Lucene | Định dạng index chỉ tương thích ngược 1 phiên bản lớn, không hợp lưu nhiều năm |
| LMDB | Không nén, phải nén từng value nên file lớn |
| bbolt, Badger, Pebble | Chỉ có Go |
| HFile | Gắn chặt với HBase |
| Lance, Vortex | Thư viện Java/Go chưa chín |
| Iceberg, Delta, Hudi | Lớp quản lý bảng, bên dưới vẫn là Parquet |
