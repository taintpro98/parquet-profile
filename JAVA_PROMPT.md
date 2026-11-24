# ColdBoy (Java + RocksDB): prompt dựng repo

> Đưa nguyên file này cho trợ lý AI hoặc dev. Yêu cầu gốc: `SYSTEM_DESIGN.md`. Số đo và lý do chọn cấu hình:
> `report.md`, `upgrade.html`. Code bench tham khảo: `impl/java/src/main/java/com/mbbank/archive/archive/RocksdbStore.java`.

## Vai trò và phạm vi

Bạn là senior Java engineer. Dựng repo **ColdBoy** mới: archive service đọc dữ liệu giao dịch từ Oracle theo ngày, ghi
mỗi ngày thành một archive RocksDB trên NAS, client chỉ đọc qua HTTP API. Viết code chạy được production, không phải
bản demo.

**Đợt này làm:** khung repo, core logic **dump** và **find**, API trong `SYSTEM_DESIGN.md`, CLI, test.

**Đợt này chưa làm:** schema của catalog DB (mục "Database design" trong `SYSTEM_DESIGN.md` để trống, không tự thiết
kế). Core logic chỉ gọi catalog qua interface (mục 7); hai bản cài đặt SQLite và Oracle để khung, chưa có bảng.

## 1. Quyết định đã chốt (không bàn lại)

| | Chọn | Ghi chú |
|---|---|---|
| Ngôn ngữ | Java 25 LTS, OpenJDK (Eclipse Temurin) | Không dùng Oracle JDK (license) |
| Archive | RocksDB qua `org.rocksdb:rocksdbjni:11.8.1` | Bản đã đo trong bench; nâng bản phải đo lại |
| DB nguồn | Oracle, `com.oracle.database.jdbc:ojdbc11:23.26.3.0.0` | Chỉ đọc |
| Catalog DB | `sqlite` hoặc `oracle`, chọn bằng config | Xem mục 7 |
| Framework | Spring Boot 4.x, chỉ dùng ở module app | Core không phụ thuộc Spring |
| Build | Maven, pin mọi phiên bản (kể cả plugin), có `mvnw` | |
| JSON | Jackson (`jackson-core` streaming ở core) | |

Không tự chế định dạng file, không nối index/trailer riêng vào file RocksDB. Archive phải mở được bằng `ldb` / `sst_dump`
chuẩn của RocksDB.

## 2. Cấu trúc repo

```
coldboy/
  pom.xml                      # parent, dependencyManagement, pin phiên bản
  coldboy-core/                # thuần Java, không Spring
    archive/                   # ArchiveWriter, ArchiveReader, RocksConfig, Layout, Manifest, Checksums
    source/                    # OracleDaySource (JDBC → luồng dòng đã sắp theo REFERENCE), SourceSchema
    record/                    # RecordEncoder (dòng → JSON bytes), ColumnType
    dump/                      # DumpJob (luồng dump một ngày), Publisher (local → NAS)
    find/                      # FindService, ReaderPool (giữ DB đang mở)
    catalog/                   # interface Catalog + model DayStatus, DayInfo, Lease
  coldboy-catalog-sqlite/      # Catalog cho SQLite (xerial sqlite-jdbc), chưa có schema
  coldboy-catalog-oracle/      # Catalog cho Oracle (ojdbc11), chưa có schema
  coldboy-app/                 # Spring Boot: REST, scheduler, security, metrics, CLI
  docs/                        # ADR, runbook
```

Một jar, hai vai chạy bằng tham số: `--role=api` (phục vụ tra cứu, nhiều bản) và `--role=dumper` (một job dump, chạy
theo lịch hoặc Kubernetes CronJob). CLI dùng chung core: `coldboy dump --date=…`, `coldboy find --date=… --ref=…`,
`coldboy verify --date=…`.

## 3. Layout archive trên NAS

```
<nas.root>/2026/11/2026-11-23.g1/     # một ngày, generation 1; đổi tên vào chỗ này là bước cuối
  db/                                 # thư mục RocksDB: CURRENT, MANIFEST-*, OPTIONS-*, IDENTITY, *.sst
  manifest.json                       # ngày, số dòng, schema cột, phiên bản rocksdbjni, cấu hình nén, thời điểm dump
  SHA256SUMS                          # định dạng sha256sum, đường dẫn tương đối: `sha256sum -c SHA256SUMS` chạy được
```

- Thư mục `db/` không chứa `LOG*`, `LOCK`, WAL: build với log và WAL ở ổ cục bộ (mục 4), chỉ copy file dữ liệu.
- **Không bao giờ ghi lại một generation đã có.** Dump lại một ngày tạo `g2`, `g3`…; catalog trỏ sang generation mới
  sau khi verify xong. Generation cũ giữ lại đến khi admin xoá (bản đang được đọc không bị rút khỏi tay reader).
- Reader mở NAS ở chế độ chỉ đọc, và phải chạy được khi NAS được mount `ro` (test bắt buộc).

## 4. Dump một ngày (core logic)

Đầu vào: ngày `D` (theo múi giờ `coldboy.source.zone`, mặc định `Asia/Ho_Chi_Minh`). Mỗi bước lỗi thì cả ngày lỗi,
không để lại archive dở.

1. **Lấy lease** trong catalog cho ngày `D` (chỉ một job dump; lease có hạn và gia hạn định kỳ). Trạng thái
   `pending → dumping`. Chỉ dump khi đã qua `D + 1` cộng `coldboy.dump.lag` (mặc định 2 giờ) để dữ liệu muộn vào đủ.
2. **Mở Oracle, snapshot nhất quán:** `setReadOnly(true)`, `SET TRANSACTION READ ONLY`, `ALTER SESSION SET NLS_SORT =
   BINARY`. Kiểm tra charset DB là `AL32UTF8` (khác thì dừng: thứ tự byte UTF-8 của key phải trùng thứ tự Oracle).
   Trong cùng transaction: `SELECT COUNT(*)` rồi
   `SELECT … FROM <bảng> WHERE CREATED_AT >= :d AND CREATED_AT < :d1 ORDER BY REFERENCE` (dùng khoảng, không `TRUNC`,
   để dùng index `(CREATED_AT, REFERENCE)`). Fetch size 1.000 (đã đo). Gặp `ORA-01555` thì chạy lại cả ngày; ghi
   trong runbook yêu cầu `UNDO_RETENTION` ≥ thời gian dump.
3. **Kiểm schema nguồn** từ `ResultSetMetaData` so với schema khai báo (73 cột, tên, kiểu). Lệch thì dừng, không đoán.
4. **Mã hoá từng dòng** thành value JSON (một object, đủ 73 cột, thứ tự cố định), ghi thẳng ra UTF-8 bằng
   `JsonGenerator` vào một buffer byte dùng lại (không đi `StringBuilder → String → byte[]`; bench cho thấy bước chuyển
   chuỗi là phần chậm của Java). Giữ nguyên giá trị:
   - `NUMBER` → `getBigDecimal`, ghi số JSON dạng `toPlainString()` (không qua `double`).
   - `DATE` / `TIMESTAMP` → `getObject(…, LocalDateTime.class)`, chuỗi định dạng cố định `uuuu-MM-dd HH:mm:ss.SSSSSS`
     (không phụ thuộc múi giờ JVM). `TIMESTAMP WITH TIME ZONE` → `OffsetDateTime`, ISO-8601.
   - `NULL` → `null`. Oracle coi `''` là `NULL`, ghi đúng như Oracle trả.
   - `RAW` / `BLOB` (nếu có) → base64. `CLOB` → chuỗi.
5. **Ghi SST bằng `SstFileWriter`** ra thư mục làm việc cục bộ `coldboy.work.dir`, key = `REFERENCE` (UTF-8). Kiểm
   từng key tăng dần nghiêm ngặt theo byte: key trùng hoặc lùi thì dừng và báo rõ key (không bỏ qua; quy tắc trùng
   key hỏi chủ nguồn). Chia file SST khi vượt `coldboy.dump.sst-target-bytes` (mặc định 4 GiB, chưa đo ở 10M dòng: đo
   lại trước khi chốt), các file không chồng khoảng key.
6. **Nạp vào DB:** mở DB mới ở `work/<D>.g<N>/db` với `wal_dir` và `db_log_dir` ở ổ cục bộ ngoài thư mục đó (hoặc
   gắn `org.rocksdb.Logger` đẩy log sang SLF4J), `ingestExternalFile(tất cả SST, moveFiles=true)` một lần, đóng DB.
7. **Kiểm số dòng:** số dòng đã ghi = `num_entries` trong table properties = `COUNT(*)` ở bước 2. Lệch thì lỗi.
8. **Ghi `manifest.json`, fsync** mọi file và thư mục (`FileChannel.force(true)`, kể cả thư mục), tính SHA-256 từng file,
   ghi `SHA256SUMS`.
9. **Đẩy lên NAS:** copy sang `<nas>/YYYY/MM/<D>.g<N>.partial-<uuid>/`, vừa copy vừa băm, fsync; đọc lại từ NAS và so
   SHA-256; `Files.move(…, ATOMIC_MOVE)` thành `<D>.g<N>/`, fsync thư mục cha. Lỗi ở bất kỳ bước nào: xoá thư mục
   `.partial-*`, giữ nguyên generation cũ.
10. **Verify sau publish:** mở bản trên NAS read-only, `verifyChecksum()`, lấy mẫu `coldboy.dump.sample` key (mặc định
    1.000, chọn ngẫu nhiên từ Oracle) và so từng byte với Oracle.
11. **Cập nhật catalog** trong một thao tác: generation hiện tại = `g<N>`, trạng thái `archived`, số dòng, kích thước,
    danh sách file + SHA-256. Xoá thư mục làm việc cục bộ. Trả lease.

Dump lại (`POST /v1/days/{date}/dump`) chạy đúng luồng trên với `N + 1`. ColdBoy không bao giờ xoá dữ liệu ở Oracle.
Job hỏng giữa chừng (pod chết): lease hết hạn, lần chạy sau dọn `.partial-*` và thư mục làm việc cũ rồi chạy lại.

## 5. Cấu hình RocksDB (đã đo, giữ nguyên)

| Tham số | Giá trị |
|---|---|
| Nén | ZSTD mức mặc định (3), cả `compression` và `bottommost_compression` |
| ZSTD dictionary | `max_dict_bytes` 64 KB, `zstd_max_train_bytes` 6,4 MB, `max_dict_buffer_bytes` 64 MB, `use_zstd_dict_trainer=true` |
| Block | 64 KB |
| Index / filter | `kTwoLevelIndexSearch`, `partition_filters=true`, `metadata_block_size` 4096, bloom 10 bit/key |
| Cache | `cache_index_and_filter_blocks=true` (ưu tiên cao), `pin_top_level_index_and_filter=true` |
| Ghim định dạng | đặt tường minh `format_version` và `checksum=kXXH3` để nâng thư viện không đổi định dạng ngầm |

- rocksdbjni 11.8.1 không có setter `max_dict_buffer_bytes`: dựng `ColumnFamilyOptions` qua
  `getColumnFamilyOptionsFromProps` với `ConfigOptions.setIgnoreUnknownOptions(false)` (sai tên tham số thì lỗi ngay),
  như `RocksdbStore.java`. Sau dump, test kiểm bằng `sst_dump --show_properties` rằng file có khối dictionary.
- Mọi đối tượng native (`Options`, `Cache`, `BloomFilter`, `RocksDB`, `ReadOptions`…) đóng tường minh bằng
  try-with-resources hoặc vòng đời của bean; không để GC dọn.
- Tham số chỉnh được qua config nhưng giá trị mặc định là bảng trên; ghi tham số thực tế vào `manifest.json`.

## 6. Find (core logic)

- `GET /v1/records/{reference}?created_at=…`: `created_at` → ngày `D` (cùng múi giờ với dump) → catalog cho
  generation hiện tại (cache ngắn hạn, ví dụ 30 s) → `ReaderPool` lấy DB đang mở → `get`. Không có ngày hoặc không có
  key → `404`. Ngày chưa `archived` → `404` kèm mã lỗi riêng (`DAY_NOT_ARCHIVED`).
- `POST /v1/records:lookup`: tối đa 1.000 key (vượt thì `400`), bỏ key trùng, một lần `multiGetAsList`, trả theo thứ
  tự yêu cầu, key không có thì bỏ qua.
- **Trả thẳng byte JSON đã lưu**, không parse lại rồi serialize: nhanh hơn, ít RAM, và đúng từng byte với lúc dump.
  Lookup ghép các value thành mảng JSON bằng cách nối byte.
- **Checksum khi đọc:** `ReadOptions.setVerifyChecksums(true)` (RocksDB kiểm checksum từng block đã đọc). Lỗi
  `Status.Code.Corruption` → `500` với mã `ARCHIVE_CORRUPT`, log + metric + cảnh báo, không bao giờ trả dữ liệu.
- **`ReaderPool`:** giữ DB read-only đã mở theo `(ngày, generation)`, tối đa `coldboy.reader.max-open-days`
  (mặc định 32), loại theo LRU. Đếm tham chiếu: chỉ `close()` khi không còn request nào đang dùng. Một `LRUCache`
  dùng chung cho mọi DB (mặc định 512 MiB) để RAM native có trần. Mở DB với `openReadOnly`, `errorIfWalFileExists`
  false, log ra SLF4J (không tạo file trên NAS), `allow_mmap_reads=false`, `skip_stats_update_on_db_open=true`.
- Giới hạn số lời gọi RocksDB đồng thời bằng semaphore (mặc định 2 × số CPU); quá hạn chờ thì `503`. Nếu dùng virtual
  thread: lời gọi JNI ghim carrier thread, nên semaphore là bắt buộc.
- Mục tiêu: tra 1 key dưới 1 s ở service đã chạy (bench: 0,02–0,4 s kể cả khởi động JVM), không tăng theo kích thước
  ngày.

## 7. Catalog: hai option, chưa thiết kế schema

Core chỉ biết interface; bản cài đặt chọn bằng `coldboy.catalog.type=sqlite|oracle`. Migration bằng Flyway (chạy được
cả hai), file migration để trống đến khi chốt "Database design".

```java
public interface Catalog {
    Optional<Lease> tryAcquireDumpLease(LocalDate day, Duration ttl);   // một job dump
    void renewLease(Lease lease);
    void markDumping(Lease lease, int generation);
    void markArchived(Lease lease, DayInfo info);                        // một transaction: generation hiện tại + trạng thái + file + SHA-256
    void markFailed(Lease lease, String reason);
    Optional<DayInfo> day(LocalDate day);
    List<DayInfo> days(LocalDate from, LocalDate to);
    void recordVerify(LocalDate day, int generation, VerifyResult result);
}
```

| | SQLite | Oracle |
|---|---|---|
| Hợp với | 1 bản ColdBoy, dev, môi trường nhỏ | Prod, nhiều bản `api` + một `dumper` |
| Đặt ở đâu | File trên ổ cục bộ (PVC) của pod; **không đặt trên NAS** (khoá file SQLite trên NFS không an toàn) | Schema riêng, user riêng, tách khỏi DB nguồn |
| Đồng thời | WAL mode, `busy_timeout`; nhiều pod không chia sẻ được | Lease bằng `SELECT … FOR UPDATE SKIP LOCKED` hoặc cột hết hạn |
| Vận hành | Không cần DBA; backup bằng `VACUUM INTO` | Theo quy trình DBA sẵn có |

Khuyến nghị: Oracle cho prod (nhiều bản đọc đồng thời theo yêu cầu), SQLite cho dev và chạy một bản. Test core chạy
được với cả hai qua cùng một bộ contract test.

## 8. Bộ nhớ (pod 4 CPU / 4 GiB)

- JVM: `-Xmx1g` (bench dump đỉnh ~605 MB RSS với `-Xmx2g`, heap thực dùng nhỏ), `-XX:MaxDirectMemorySize=256m`.
- Native: block cache 512 MiB dùng chung, buffer dictionary 64 MiB khi dump, `SstFileWriter` theo block.
- `MALLOC_ARENA_MAX=2` (hoặc jemalloc) để glibc không phình RAM native khi RocksDB cấp phát nhiều luồng.
- Metric RSS và `rocksdb.block-cache-usage`; test tải xác nhận tổng dưới 3 GiB, chừa đầu cho dump và tra cùng lúc.
- Ổ làm việc cục bộ: tối thiểu 2 × dung lượng một ngày (~60–74 GB/ngày theo `SYSTEM_DESIGN.md`).

## 9. Vận hành, bảo mật, quan sát (mức tối thiểu đợt này)

- `GET /health`: liveness tách khỏi readiness; readiness kiểm catalog, NAS đọc được, Oracle (chỉ ở vai `dumper`).
- `POST /v1/days/{date}/verify`: tính lại SHA-256 từng file trên NAS so với catalog, thêm `verifyChecksum()`; ghi kết quả.
- TLS, xác thực client (mTLS hoặc OAuth2 JWT), quyền `admin` tách riêng cho dump/verify; log truy vấn (ai, ngày, số
  key, mã kết quả; không log nội dung bản ghi). Kết nối Oracle bật mã hoá (Native Network Encryption hoặc TCPS).
- Mật khẩu Oracle từ secret/biến môi trường, không nằm trong repo hay log.
- Metric Micrometer/Prometheus: thời gian và số dòng dump, kích thước archive, độ trễ tra p50/p99, số lỗi theo mã.
- Mã lỗi API thống nhất: `{"code": "...", "message": "..."}`.

## 10. Test bắt buộc

Dùng JUnit 5 + Testcontainers (Oracle Free, ví dụ `gvenzl/oracle-free`), dữ liệu seed **lặp nhiều** như dữ liệu thật.

- Dump rồi tra: bản ghi trả về khớp từng byte với JSON dựng từ Oracle (1 key, 1.000 key, key không tồn tại).
- Giữ nguyên giá trị: `NUMBER` lớn và nhiều chữ số thập phân, `NULL`, timestamp có micro giây, chuỗi Unicode, ký tự điều khiển.
- Thứ tự key: `REFERENCE` có ký tự ngoài ASCII; key trùng → dump lỗi rõ ràng.
- Nguyên tử: giết tiến trình ở từng bước 5–9 → không có thư mục `<D>.g<N>/` dở, lần sau chạy lại thành công.
- Hỏng dữ liệu: lật một byte trong file SST → tra trả `ARCHIVE_CORRUPT`, `/verify` báo sai SHA-256.
- NAS chỉ đọc: mount `ro`, mở và tra được, không có file nào được tạo trên NAS.
- Generation: dump lại khi reader đang đọc `g1` → request đang chạy không lỗi, request mới đọc `g2`.
- Catalog: cùng contract test cho SQLite và Oracle.
- Định dạng chuẩn: `ldb --db=… --readonly get <ref>` và `sha256sum -c SHA256SUMS` chạy được trên archive.
- Kiểm cấu hình: `sst_dump --show_properties` có ZSTD, dictionary, partitioned index.

## 11. Tiêu chí xong

- `./mvnw verify` xanh, gồm test tích hợp với Oracle container.
- `coldboy dump --date=…` trên bộ seed 1M dòng cho dung lượng và thời gian cùng cỡ với bench (5,4 GB, ~400 s); lệch
  nhiều thì báo cáo nguyên nhân.
- README ngắn: chạy local (docker-compose Oracle + SQLite), cấu hình, runbook dump lại / verify / khôi phục.
- ADR trong `docs/` cho các quyết định ở mục 1, 3, 5 và phần catalog.

## 12. Không làm

- Không thêm truy vấn khác ngoài `REFERENCE` + `CREATED_AT` (không lọc cột khác, không đếm, không thống kê).
- Không ghi đồng thời, không cập nhật hay xoá bản ghi trong archive, không ghi thêm vào generation đã publish.
- Không tự thiết kế schema catalog.
- Không đổi định dạng value, codec hay cấu hình RocksDB khi chưa đo lại bằng bộ bench của repo này.
