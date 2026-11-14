# parquet-profile

So sánh các phương án **lưu và nén** dữ liệu giao dịch lạnh (cold archive), viết bằng Java và Go.

**Bài toán:** đưa vào **key (`REFERENCE`) + timestamp (`CREATED_AT`)**, lấy ra **một bản ghi đầy đủ**
(73 cột). Dữ liệu chia theo **ngày**: mỗi ngày khoảng 10 triệu dòng nằm trong **một file**
`YYYY-MM-DD.<đuôi>`. Timestamp chọn file của ngày, key tìm bản ghi trong file đó.

Mỗi phương án làm hai việc:

- **DUMP**: đọc Oracle theo (ngày, `REFERENCE`), ghi mỗi ngày ra một file.
- **FIND**: với danh sách key + timestamp, trả về bản ghi có đúng key và đúng timestamp.

## Chỉ số

| Chỉ số | Ý nghĩa |
| --- | --- |
| Wall (s) | Thời gian thực từ lúc khởi động tiến trình tới khi kết thúc |
| CPU (s) | user + sys của tiến trình, tính mọi thread |
| Peak RAM (MB) | RSS cao nhất của cả tiến trình (heap, native, DuckDB…) |
| File (MB) | Kích thước archive (DUMP) |
| Đúng | DUMP ghi được dòng, FIND trả đúng số bản ghi mong đợi |

Runner đo **từ bên ngoài tiến trình** (`wait4`/rusage), nên Java và Go được đo cùng một cách, và số đo
đã gồm cả khởi động JVM, DuckDB, kết nối Oracle.

## Lệnh

Tất cả chạy từ thư mục gốc. Cần Docker, Python 3, Go, JDK 25 và Maven (Makefile tự tìm).

```bash
make seed                            # sinh 100k dòng ngẫu nhiên vào Oracle (Docker) + golden + bench/refs
make seed-clean                      # xoá dữ liệu cũ (Oracle, archive, refs) để giải phóng ổ đĩa
make seed-archives IMPL=all          # dump archive một ngày cho từng phương án (lâu, tốn ổ đĩa, xem "Dữ liệu")
make list                            # các phương án hiện có
make test                            # test đúng/sai, mọi phương án (IMPL=... để chọn)

make dump  IMPL=go-parquet-duckdb
make find  IMPL=go-parquet-duckdb SCENARIO=many-1000 CACHE=cold RUNS=5
make bench IMPL=all                  # dump rồi find mọi kịch bản
make report                          # tạo lại results/REPORT.md
```

| Tham số | Giá trị | Mặc định |
| --- | --- | --- |
| `IMPL` | tên phương án, danh sách cách nhau dấu phẩy, hoặc `all` | `all` |
| `SCENARIO` | tên file trong `bench/refs/`, danh sách, hoặc `all` | `all` |
| `CACHE` | `warm` (chạy làm nóng 1 lần, không ghi) hoặc `cold` (xoá cache OS trước mỗi lần, cần `sudo -n`) | `warm` |
| `RUNS` | số lần đo | dump 1, find 5 |
| `LABEL` | tên cho một cấu hình, tách riêng file archive và dòng trong báo cáo | trống |

Cấu hình đi qua biến môi trường, ví dụ thử row group nhỏ hơn:

```bash
make bench IMPL=java-parquet-duckdb PARQUET_ROW_GROUP_ROWS=50000 LABEL=rg50k
```

## Kết quả

```
results/REPORT.md      ← bảng so sánh, tự tạo lại sau mỗi lần chạy
results/results.csv    ← mỗi lần đo một dòng, chỉ ghi thêm
results/runs/<impl>/<thao tác>-<kịch bản>-<thời điểm>-<lần>/
    metrics.json  output.log  result.jsonl
data/<impl>[-<label>]/YYYY-MM-DD.<ext>   ← mỗi ngày một file
```

Trong `REPORT.md`, mỗi (thao tác, kịch bản, cache, phương án, nhãn) lấy lần bench gần nhất: thời gian
là trung vị, RAM là giá trị lớn nhất, ô tốt nhất của mỗi cột in đậm. Đo số liệu thật trên Linux với
giới hạn giống pod; RSS trên macOS có thể thấp hơn thực tế khi máy thiếu RAM.

## Cấu trúc

```
Makefile
bench/bench.py          runner: đo, ghi CSV, tạo REPORT.md
bench/refs/*.txt        kịch bản FIND do make seed sinh ("missing*" phải trả 0 dòng), không commit
testdata/golden.py      sinh golden.sql (bảng ARCHIVE_GOLDEN) và golden.jsonl (kết quả mong đợi)
impl/java/              Maven
  common/               Cli, OracleSource, DaySplitter, Lookup, Row, RowCursor, Jsonl, Bytes, Settings,
                        Archive (interface)
  archive/              mỗi phương án 1 file + Archives (đăng ký)
impl/go/
  common/               giống Java
  archive/              mỗi phương án 1 file + registry.go
seed/seed.sh            seed, clean, archives (gọi qua make)
seed/gen/               bộ sinh dữ liệu ngẫu nhiên (Go)
docs/                   báo cáo cũ
```

### Thêm một phương án

1. Viết một file trong `impl/<ngôn ngữ>/.../archive/` cài đặt interface `Archive`: `dump` ghi các dòng
   của **một ngày** (đã sắp theo `REFERENCE`) ra một file, `find` trả các bản ghi có key cần tìm trong
   một file ngày. Chia theo ngày và lọc theo timestamp là việc của phần chung.
2. Thêm một dòng vào `Archives.java` hoặc `registry.go`.
3. `make test IMPL=<ngôn ngữ>-<tên>`; test hợp đồng tự chạy cho phương án mới.

### Hợp đồng chung

```
<cli> list                                                    → "<tên>\t<đuôi file>" mỗi dòng
<cli> dump --impl NAME --archive DIR                              → dòng cuối {"rows":N}
<cli> find --impl NAME --archive DIR --refs LOOKUPS --out OUT    → dòng cuối {"found":N}
```

`DIR` chứa mỗi ngày một file `YYYY-MM-DD.<đuôi>`. DUMP ghi từng ngày ra `.tmp` rồi mới đổi tên, nên
một lần dump lỗi không làm hỏng ngày đã lưu.

`LOOKUPS` mỗi dòng một `REFERENCE,YYYY-MM-DD HH:MM:SS.ffffff`. FIND mở file của ngày trong timestamp,
tìm key, và chỉ trả bản ghi khi `CREATED_AT` trùng đúng timestamp. Ngày không có file thì không có kết
quả.

FIND ghi `OUT` dạng JSON Lines, sắp theo `reference`, **giống từng byte** với
`json.dumps(record, ensure_ascii=False, separators=(",", ":"))` của Python: khoá theo thứ tự
`id, reference, created_at, text_01..text_70`, `created_at` dạng `YYYY-MM-DD HH:MM:SS.ffffff`,
NULL là `null`.

### Test đúng/sai

`testdata/golden.py` sinh 300 dòng có ca biên: NULL, chuỗi rỗng (Oracle lưu thành NULL), ký tự cần
escape, ký tự điều khiển, tiếng Việt, emoji 4 byte, U+2028, chuỗi 1000 byte, ID = int64 max, thời
điểm trước 1970 và năm 9999, reference có thứ tự byte khác thứ tự ngôn ngữ. 90% số dòng thuộc một ngày,
phần còn lại rải ra nhiều ngày, nên vừa có nhiều file ngày vừa có một file ngày lớn.

Test của mỗi ngôn ngữ dump bảng này, find mọi key + timestamp cùng các lookup phải trả rỗng (key không
có, đúng key sai giờ, đúng key sai ngày, ngày không có file), rồi so kết quả với `golden.jsonl` từng
dòng. `make test` đặt row group / lô / block rất nhỏ để file ngày lớn có nhiều row group, lô và block.

## Biến môi trường

| Biến | Mặc định | Dùng cho |
| --- | --- | --- |
| `ORACLE_URL` | `jdbc:oracle:thin:@//localhost:1521/FREEPDB1` | mọi phương án (Go đọc cùng dạng URL) |
| `ORACLE_USERNAME` / `ORACLE_PASSWORD` | `benchmark` | tài khoản test cục bộ, không commit mật khẩu thật |
| `ORACLE_TABLE` | `ARCHIVE_PROFILE_DATA` | bảng nguồn |
| `ORACLE_QUERY` | trống | thay câu SELECT; cùng cột, theo thứ tự (ngày của `CREATED_AT`, `REFERENCE`) |
| `ORACLE_REPEAT` | `1` (Makefile đặt `100`) | đọc bảng N lần thành một ngày, xem "Dữ liệu" |
| `ORACLE_FETCH_SIZE` | `1000` | số dòng mỗi lượt đọc |
| `ORACLE_TEXT_COLUMNS` | `70` | số cột `TEXT_nn` |
| `PARQUET_COMPRESSION` | `ZSTD` | các phương án Parquet |
| `PARQUET_ROW_GROUP_ROWS` | `250000` | các phương án Parquet |
| `PARQUET_ROW_GROUP_BYTES` | 2 GiB | java-parquet-duckdb |
| `DUCKDB_THREADS` | `4` | các phương án DuckDB |
| `DUCKDB_EXTERNAL_FILE_CACHE` | `false` | các phương án DuckDB |
| `ARROW_BATCH_ROWS` | `1024` | số dòng mỗi lô Arrow |
| `BLOCK_BYTES` | `1048576` | kích thước block trước khi nén (rowblock) |
| `BLOCK_ZSTD_LEVEL` | `3` | mức nén ZSTD (rowblock) |
| `JAVA_OPTS` | trống | tham số JVM, ví dụ `-Xmx2g` |

Các biến ảnh hưởng tới số đo được ghi kèm mỗi lần chạy, trong cột `settings` của CSV.

## Phương án hiện có

Ba kiểu lưu, mỗi kiểu có bản Java và bản Go (tên `java-…` / `go-…`):

| Phương án | Kiểu lưu | Nén | Tìm một key trong file ngày |
| --- | --- | --- | --- |
| `parquet-duckdb` | cột, có mã hoá (dictionary, RLE…) | ZSTD | DuckDB: `WHERE reference IN (...)`, bỏ qua row group nhờ min/max |
| `arrow-zstd` | cột, Arrow IPC, không mã hoá | ZSTD từng buffer | index lô → giải nén đúng lô `ARROW_BATCH_ROWS` dòng chứa key |
| `rowblock-zstd` | dòng, block nhị phân | ZSTD từng block | index block → giải nén đúng block `BLOCK_BYTES` chứa key |

Ghi Parquet: Java dùng parquet-java (chép thẳng từ JDBC vào cột), Go dùng parquet-go; cả hai đọc bằng
DuckDB nhúng. Arrow: arrow-java và arrow-go. Rowblock: tự viết, ZSTD qua zstd-jni và klauspost/compress.

Định dạng `arrow-zstd` và `rowblock-zstd` giống hệt nhau giữa Java và Go, nên mỗi bên đọc được file của
bên kia.

### `arrow-zstd`: Arrow IPC + index lô

```
[file Arrow IPC đầy đủ, nén ZSTD]
[index]   uvarint số lô | mỗi lô: uvarint độ dài + reference đầu tiên của lô
[trailer] uint64 LE độ dài phần Arrow | "ARCHARW1"
```

Arrow không lưu min/max, nên index nằm sau phần Arrow. Phần Arrow vẫn là file Arrow chuẩn: cắt
file ở độ dài trong trailer là mọi công cụ Arrow đọc được.

### `rowblock-zstd`: block theo dòng + index thưa

```
[block 0][block 1]...      mỗi block là một frame ZSTD chứa các dòng liên tiếp
  dòng = int64 LE id | int64 LE created_at (micro giây) | uvarint độ dài + reference |
         mỗi cột text: uvarint (độ dài + 1, 0 là NULL) + byte UTF-8
[index]   uvarint số block | uvarint số cột text |
          mỗi block: uvarint độ dài + reference đầu | uvarint offset | uvarint cỡ nén | uvarint cỡ gốc
[trailer] uint64 LE vị trí index | "ARCHBLK1"
```

## Dữ liệu

`make seed` bật Oracle Free trong Docker (datafile ở `data/oracle`) và sinh **đúng 100.000 dòng
ngẫu nhiên** vào `ARCHIVE_PROFILE_DATA`, chèn không theo thứ tự `REFERENCE`. Mất khoảng 1,5 phút,
chiếm khoảng 2,3 GB trong Oracle (dòng trung bình khoảng 21 KB, bằng bộ dữ liệu cũ).

- Bảng **đã có dữ liệu thì seed không làm gì**. Muốn sinh bộ mới phải xoá trước bằng `make seed-clean`.
  Lệnh này hỏi xác nhận (bỏ qua bằng `YES=1`), rồi xoá container, `data/` (Oracle và mọi archive, vì
  archive sinh từ dữ liệu cũ) và `bench/refs`.
- Mỗi lần sinh in ra `SEED=...`; `SEED=<số> make seed` sinh lại đúng bộ đó.
- Toàn bộ dòng thuộc **một ngày** (2026-11-14), `CREATED_AT` ngẫu nhiên trong ngày, chính xác tới micro
  giây. `REFERENCE` có dạng `FT26` + 12 chữ số, duy nhất.
- Kích thước giống bộ dữ liệu cũ: mỗi cột text 100–499 byte, `TEXT_02` NULL khoảng 1/3, `TEXT_03` là
  ghi chú ngắn. Nội dung thì ngẫu nhiên: mỗi cột mở đầu theo một kiểu cố định theo vị trí (họ tên, số
  tài khoản, số tiền, mã, địa chỉ, trạng thái, metadata JSON), phần còn lại là câu ghép từ tiếng Việt
  ngẫu nhiên. Nội dung không lặp lại nên nén kém hơn dữ liệu cũ, gần với dữ liệu thật hơn.

**Một ngày 10M dòng** = đọc bảng 100k dòng `ORACLE_REPEAT` lần (Makefile mặc định 100). Mỗi lượt thêm số
lượt 4 chữ số vào đầu `REFERENCE` và cộng `lượt × 1.000.000.000` vào `ID`, nên file ngày vẫn sắp xếp và
không trùng. Oracle chạy truy vấn và `ORDER BY` mỗi lượt, nhưng từ lượt 2 đọc từ buffer cache.
`make seed-archives IMPL=...` dump ngày này (fetch 10000) vào `data/<impl>/2026-11-14.<đuôi>`.

Seed cũng ghi `bench/refs/` cho đúng `ORACLE_REPEAT` lúc seed: `one` (1 lookup), `many-1000` (1000
lookup ngẫu nhiên) và `missing` (6 lookup phải trả rỗng: trước đầu file, sau cuối file, key không có,
đúng key sai giờ, đúng key ngày không có file). Đổi `ORACLE_REPEAT` thì phải seed lại.

**Dung lượng ổ đĩa:** dữ liệu ngẫu nhiên chỉ nén được khoảng 3 lần. Chạy thử 200k dòng cho ra khoảng
1,35 GB mỗi phương án, nên **một ngày 10M dòng tốn khoảng 65 GB cho mỗi phương án**, khoảng 400 GB cho
cả 6. Trên máy ít chỗ trống, hãy giảm ngày (ví dụ `ORACLE_REPEAT=10` khi seed và khi dump, khoảng 6,7 GB
mỗi phương án), hoặc bench từng phương án rồi xoá `data/<impl>` trước khi sang phương án tiếp theo.

## Giới hạn

- CPU và RAM của Oracle không được đo.
- Không có index trên `REFERENCE` thì Oracle phải sắp xếp toàn bộ trong TEMP trước khi trả dòng đầu.
- Truy vấn dài cần đủ undo retention, nếu không sẽ gặp `ORA-01555`.
- FIND chịu ảnh hưởng của cache hệ điều hành; dùng `CACHE=cold` để so sánh lần đọc đầu.
