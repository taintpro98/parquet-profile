# Metrics benchmark

**Bài toán:** key (`REFERENCE`) + timestamp (`CREATED_AT`) → một bản ghi đầy đủ. Mỗi ngày một file.

## 4 thao tác

| Thao tác | Mô tả |
| --- | --- |
| `dump` | Oracle → 1 file ngày |
| `find-one` | tìm 1 key có thật trong 1 file |
| `find-1000` | tìm 1000 key trong 1 file |
| `find-unknown` | tìm 1 key không tồn tại trong 1 file |

## Metric cho mỗi thao tác

| Metric | Đơn vị |
| --- | --- |
| Wall | giây |
| CPU | giây |
| Peak RAM | MB |
| Storage | MB (dung lượng file ngày) |

Mọi thao tác phải trả kết quả đúng:
- `find-one`, `find-1000`: key chọn ngẫu nhiên từ Oracle mỗi lần chạy; bản ghi trả về phải khớp từng
  cột với Oracle.
- `find-unknown`: phải trả rỗng.

## Metric định tính

| Metric | Câu hỏi |
| --- | --- |
| Stability | Định dạng và thư viện có ổn định qua các phiên bản không? Đọc lại được file sau nhiều năm không? |
| Thân thiện với dev | Tra một key cần bao nhiêu code? Mở xem được bằng công cụ chuẩn (SQL, CLI) không? |
| Prod ready | Thư viện chính thức, đã chạy production ở đâu? Có checksum, dễ vận hành, dễ giới hạn RAM không? |

## 2 kịch bản dữ liệu

| Kịch bản | Mô tả |
| --- | --- |
| `random` | mọi cột text ngẫu nhiên |
| `status` | 20% số cột là status (ít giá trị, lặp nhiều) |

## Quy mô

Đo ở **1M** dòng, cần thì đo thêm **2M**, rồi suy ra **10M**:
- RAM(10M) = RAM(1M) + 9 × (RAM(2M) − RAM(1M))
- Storage(10M) = Storage(1M) × 10
