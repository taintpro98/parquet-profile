# Archive ColdBoy: kết luận

Một ngày 1M dòng, dữ liệu lặp. Ưu tiên: thời gian ≈ dung lượng > RAM; khi chọn cấu hình nén thì dung lượng nặng hơn, chấp nhận thời gian tăng vài %.
Mọi số dưới đây đo trong **một phiên**, trên **một bộ dữ liệu** (seed cố định `20261117`, xem `results/status/seed.json`),
cùng máy, cùng giao thức: dump 3 lần lấy trung vị, rồi tra 1 key / 1000 key / key không tồn tại; lô đọc Oracle 1.000
cho cả ba. Chi tiết và bảng chọn cấu hình: `upgrade.html`. License: `license.html`.

## Chọn: go-rocksdb + ZSTD dictionary 64 KB

| | go-rocksdb | java-rocksdb | java-parquet-duckdb |
|---|---|---|---|
| Dung lượng file | 5,4 GB | 5,4 GB | **5,3 GB** |
| Tra 1 key | **0,02 s** | 0,44 s | 1,54 s |
| Tra 1000 key | **0,37 s** | 0,79 s | 42,8 s |
| Tra 1000 key · RAM đỉnh | **160 MB** | 341 MB | 2,26 GB |
| Tra key không tồn tại | **0,02 s** | 0,41 s | 1,46 s |
| Dump · thời gian | 335 s | 403 s | **334 s** |
| Dump · CPU | **191 s** | 244 s | 214 s |
| Dump · RAM đỉnh | **293 MB** | 605 MB | 788 MB |
| Khớp Oracle từng byte | 1000/1000 | 1000/1000 | 1000/1000 |

Thời gian tra gồm khởi động tiến trình (Java ~0,4 s; java-parquet-duckdb thêm ~1 s mở DuckDB).

- **go-rocksdb:** tra nhanh nhất, RAM thấp nhất, file chỉ lớn hơn Parquet 2,5%.
- **java-rocksdb:** file và cách nén y hệt Go; tra 1000 key chậm hơn 2,2 lần (chủ yếu do khởi động JVM), dump chậm hơn
  20%, RAM gấp đôi. Phương án dự phòng nếu bắt buộc dùng Java.
- **java-parquet-duckdb:** file nhỏ nhất nhưng tra 1000 key chậm hơn Go 117 lần và tăng theo kích thước file. Loại.
- Cả ba không dính Oracle JDK hay license tính phí.

## Cấu hình của từng phương án

| | go-rocksdb (winner) | java-rocksdb | java-parquet-duckdb |
|---|---|---|---|
| Định dạng | RocksDB, key = `REFERENCE`, value = JSON 73 cột | như Go | Parquet, sort theo `REFERENCE`, tra bằng DuckDB |
| Nén | ZSTD mức 3, block 64 KB | như Go | ZSTD mức 3, dictionary encoding, row group 128 MB |
| Dictionary | 64 KB, train 6,4 MB, buffer 64 MB | như Go, đặt qua parser options native | không áp dụng |
| Index / bloom | index và bloom phân vùng, bloom 10 bit/key, cache 64 MiB | như Go | min/max của row group |
| Cách ghi | `SstFileWriter` rồi ingest, 1 writer | như Go, JSON ghi bằng `StringBuilder` | một file Parquet cho cả ngày |
| Đọc Oracle | godror, lô 1.000, tắt prefetch | ojdbc, lô 1.000 | ojdbc, lô 1.000 |
| Runtime | `GOMEMLIMIT=2GiB` | `-Xmx2g` (OpenJDK) | `-Xmx2g` (OpenJDK), DuckDB 4 luồng |

Tham số RocksDB đã được `sst_dump` xác nhận trong file SST (có khối dictionary). Mọi tham số chỉnh được bằng biến môi
trường, danh sách ở cuối `upgrade.html`; chạy lại bằng `bench/run_final.sh`.

## Vì sao chọn các cấu hình này (bước chọn cấu hình, mỗi cấu hình dump 1 lần; "tốt nhất" là tốt nhất trong các cấu hình đã thử)

- **Dictionary 64 KB so với tắt:** file nhỏ hơn 9,1% (dict 16 KB: 4,7%). Cái giá: tra 1000 key chậm hơn 12%
  (0,33 → 0,37 s) và dump chậm hơn 20% (279 → 334 s, một lần lúc ghi). Chọn 64 KB vì file nhỏ hơn rõ rệt, chênh lệch
  tra chỉ vài chục ms cho 1000 key. Nếu ưu tiên thời gian tra tuyệt đối hơn dung lượng thì tắt dictionary vẫn hợp lệ.
- **java-rocksdb, chọn riêng (cùng giao thức, dump 1 lần):**

  | | File | Tra 1000 key | Dump | RAM dump |
  |---|---|---|---|---|
  | Tắt dictionary | 5,95 GB | 0,89 s | 372 s | 485 MB |
  | Dictionary 16 KB | 5,67 GB (−4,7%) | 0,84 s | 420 s | 543 MB |
  | Dictionary 64 KB | **5,41 GB (−9,1%)** | 0,81 s | 406 s | 602 MB |

  Cùng chiều với Go: chọn 64 KB vì file nhỏ nhất. Khác Go, tra 1000 key không chậm đi (khởi động JVM ~0,4 s che
  chênh lệch nhỏ). Cái giá: dump +9%, RAM dump +24%. Mỗi cấu hình chỉ đo 1 lần nên chênh lệch vài % (tra giữa 16 KB
  và 64 KB) nằm trong nhiễu; kết luận chắc chắn là dung lượng file.
- **Parquet SNAPPY:** tra 1000 key nhanh hơn 15% nhưng file to hơn 43%, nên giữ ZSTD.
- **Parquet DuckDB 10 luồng:** không nhanh hơn 4 luồng (44,0 s so với 43,5 s), nên giữ 4 luồng.

## Vì sao số đo khác nhau

**1. Parquet nhỏ nhưng tra theo lô chậm.**
Parquet lưu theo cột, nén theo row group (~117 MB) nên nén tốt. Nhưng lấy 1 bản ghi đủ 73 cột phải giải nén cả row group
chứa nó. Tra 1 key chỉ chạm 1 row group (dữ liệu đã sort nên min/max chỉ ra đúng nó), nên gần như không tăng theo file
(1,54 s ở 1M dòng, 1,72 s ở 2M). 1000 key chạm gần hết các row group, nên thời gian tăng theo kích thước file: 42,8 s ở
1M dòng, 85,9 s ở 2M dòng. RocksDB lưu theo dòng, nén từng block 64 KB (~3 bản ghi): 1000 key chỉ đọc ~1000 block nhỏ,
không phụ thuộc kích thước file.

**2. ZSTD dictionary làm file RocksDB nhỏ hơn.**
Mỗi block nén độc lập nên các chuỗi lặp phải ghi lại ở từng block. Dictionary là một từ điển train từ mẫu dữ liệu, lưu
một lần cho cả file, để mọi block dùng chung.

**3. Java chậm hơn Go dù ra file giống hệt nhau.**
Cùng thư viện C++ RocksDB, cùng định dạng file, nên đọc và giải nén tốn như nhau. Chênh lệch nằm ở phần bọc bên ngoài:

- **Tra cứu: chủ yếu là khởi động JVM.** Tra key không tồn tại (gần như chỉ còn khởi động và mở DB): Java 0,41 s, Go 0,02 s.
  Trừ khoản này, tra 1000 key còn Java 0,38 s so với Go 0,35 s (chênh 8%). Chạy thành service thường trực thì Java không
  còn khoản khởi động.
- **Dump: Java chậm hơn 20%, CPU nhiều hơn 28%, RAM gấp đôi.** Java đi vòng nhiều bước hơn (dữ liệu thành `String`
  UTF-16, ghi JSON, đổi sang byte, chép qua JNI), Go đi từ byte ra byte; JVM còn có heap nền.
- **Bước tạo JSON đo riêng** (ngoài bộ bench, dữ liệu tổng hợp 70 cột x 300 ký tự, tính cho 1M dòng): Java bản cũ
  (`StringWriter` khoá đồng bộ từng ký tự, `String.format` mỗi cột) 230 s, Java sau khi sửa (`StringBuilder`) 70,6 s,
  Go 54 s. Bản Java trong bảng đã dùng code sau khi sửa.
- Phần dump còn lại của Java chưa profile để tách riêng từng khoản (chuyển chuỗi, JNI, GC).

## Lưu ý

- Hai bản RocksDB dùng hai bản native khác nhau: rocksdbjni đóng gói 11.8.1, Go dùng librocksdb 11.13.2 (Homebrew).
- `final.html` và các kết quả cũ trong `results/` đo trên bộ dữ liệu seed trước (hạt giống không được lưu), không so trực
  tiếp được với số trong báo cáo này.
