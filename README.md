# parquet-profile

So sánh các phương án **lưu và nén** dữ liệu giao dịch lạnh, viết bằng Java và Go.

- Bài toán và quy tắc: `CLAUDE.md`
- Metric cần đo: `METRICS.md`
- Các phương án và lý do chọn: `PROPOSAL.md`

## Lệnh

Chạy từ thư mục gốc. Cần Docker, Python 3, Go, JDK 25, Maven (Makefile tự tìm) và
`brew install rocksdb` (cho bản Go của RocksDB).

```bash
make seed SEED_MODE=random            # sinh 100k dòng vào Oracle (Docker); bộ thứ hai: SEED_MODE=status
make sample SIZE=1000                 # bốc key ngẫu nhiên từ Oracle, dùng chung cho mọi phương án

make dump         IMPL=java-rocksdb   # Oracle → 1 file ngày
make find N=1000  IMPL=java-rocksdb   # tra N key đầu của bộ đã bốc, so từng cột với Oracle
make find-unknown IMPL=java-rocksdb   # tra 1 key không tồn tại, phải trả rỗng
make bench        IMPL=all            # dump + find N=1 + find N=1000 + find-unknown

make summary                          # vẽ final.html (3 candidate) từ results/
make list                             # các phương án hiện có
```

| Tham số | Ý nghĩa | Mặc định |
| --- | --- | --- |
| `IMPL` | phương án, danh sách cách nhau dấu phẩy, hoặc `all` | `all` |
| `SEED_MODE` | bộ dữ liệu: `random` hoặc `status` (20% cột là trạng thái) | `random` |
| `ORACLE_REPEAT` | số lượt đọc bảng 100k dòng thành một ngày: 10 = 1M, 20 = 2M | `10` |
| `N` / `SIZE` | số key tra / số key bốc | `1000` |
| `RUNS` | số lần đo (find có thêm 1 lần làm nóng) | dump 1, find 3 |
| `CACHE` | `warm` hoặc `cold` (xoá cache hệ điều hành, cần `sudo -n`) | `warm` |
| `ORACLE_FETCH_SIZE` | số dòng mỗi lô fetch từ Oracle | Go (godror) `1000`, Java (ojdbc) `10000` |
| `ORACLE_REPEAT_SINGLE` | `true`: đọc cả ngày bằng một câu SELECT (`UNION ALL` các lượt) như khi chạy thật | `false` |
| `ORACLE_PREFETCH_ROWS` | Go (godror): số dòng đọc trước vào buffer riêng; `-1` = lô + 1 | `0` (tắt) |
| `LABEL` | tên cấu hình, tách archive và kết quả, ví dụ `2m` | trống |

Cấu hình từng phương án qua biến môi trường (`PARQUET_*`, `ROCKSDB_*`, `AVRO_*`…, xem đầu file của
phương án đó). Ngân sách RAM runtime đặt trong Makefile: Java `-Xmx2g`, Go `GOMEMLIMIT=2GiB`.

## Kết quả

Mỗi lệnh ghi một file và chỉ ghi đè file của phần vừa chạy:

```
bench/samples/<bộ>/r<lượt>/            bộ key đã bốc + bản ghi từ Oracle (không commit)
results/<bộ>/oracle.json               dung lượng bảng + index trong Oracle
results/<bộ>/<phương án>[-<label>]/    dump.json, find-<N>.json, find-unknown.json, logs/
data/<phương án>-<bộ>[-<label>]/       archive, mỗi ngày một file (hoặc thư mục) YYYY-MM-DD.<đuôi>
final.html                             make summary vẽ ra (3 candidate, một câu SELECT mỗi ngày)
final-report.html                      bản trước của final.html, không vẽ lại
report.html                            báo cáo cũ của mọi phương án, không vẽ lại
```

Đo từ ngoài tiến trình (`wait4`/rusage): wall, CPU, RAM đỉnh (RSS). Thời gian lấy trung vị, RAM lấy
lớn nhất qua các lần chạy. `find` so từng bản ghi trả về với Oracle và ghi số dòng khớp.

## Cấu trúc code

```
impl/java/src/main/java/com/mbbank/archive/
  common/      Archive (interface), Cli, OracleSource, DaySplitter, Lookup, Row, Jsonl…
  archive/     mỗi phương án đúng 1 file; đăng ký trong META-INF/services (ServiceLoader)
impl/go/
  common/      Archive (interface), Oracle, dump/find theo ngày, sample, JSONL
  archive/     mỗi phương án đúng 1 file, tự đăng ký trong init()
bench/         bench.py (runner), final_html.py (mẫu final.html), upgrade_html.py (mẫu upgrade.html)
seed/          seed.sh, gen/ (bộ sinh dữ liệu Go)
```

**Thêm phương án:** một file trong `archive/` cài interface `Archive` (`dump` ghi các dòng của một ngày
ra một file, `find` trả bản ghi có key cần tìm). Chia theo ngày, lọc theo timestamp, đo và kiểm toàn vẹn
là việc của phần chung.

## Phương án

| Phương án | Ngôn ngữ | Lưu | Tra một key |
| --- | --- | --- | --- |
| `parquet-duckdb` | Java, Go | Parquet, ZSTD, row group 128 MB | DuckDB `WHERE reference IN (...)` |
| `parquet-lookup` | Java, Go | Parquet + bloom filter trên reference + page 64 KB | thư viện Parquet đọc thẳng, bloom filter + page index |
| `rocksdb` | Java, Go | RocksDB, mỗi ngày một DB, ZSTD, bloom filter, index phân vùng | `multiGet` |
| `orc` | Java | ORC, ZSTD, bloom filter + row index | `SearchArgument IN` |
| `avro` | Java | Avro container, ZSTD, block 64 KB | tìm nhị phân bằng `sync(pos)` |
| `arrow-zstd`, `rowblock-zstd` | Java, Go | có phần tự chế | giữ số cũ, không đo lại |

## Dữ liệu

`make seed` sinh đúng 100.000 dòng ngẫu nhiên vào `ARCHIVE_RANDOM` / `ARCHIVE_STATUS` (khoảng 21 KB/dòng,
cùng một ngày 2026-11-14), có khoá chính `ID` và index `(REFERENCE, CREATED_AT)`. Bảng đã có thì không
sinh lại; `make seed-clean` xoá Oracle và archive (giữ `results/`). Một ngày N triệu dòng là N×10 lượt
đọc bảng đó, mỗi lượt thêm tiền tố 4 chữ số vào `REFERENCE` và cộng `lượt × 1.000.000.000` vào `ID`.
