# ColdBoy: yêu cầu

ColdBoy là archive service cho dữ liệu giao dịch lạnh: tự dump từ Oracle theo ngày lên NAS, client chỉ đọc qua API.

## Functional (API, HTTP/JSON)

- `GET /v1/records/{reference}?created_at=<timestamp>` → bản ghi đầy đủ (73 cột), `404` nếu không có; `created_at` chọn archive của ngày.
- `POST /v1/records:lookup` `{"date": "YYYY-MM-DD", "references": [...]}` → các bản ghi tìm thấy, tối đa 1000 key mỗi lần; key không có thì bỏ qua.
- `GET /v1/days/{date}` → trạng thái ngày (`pending` / `archived` / `failed`), số dòng, kích thước, danh sách file + SHA-256.
- `GET /v1/days?from=&to=` → danh sách ngày và trạng thái.
- `POST /v1/days/{date}/dump` (admin) → dump lại một ngày.
- `POST /v1/days/{date}/verify` (admin) → tính lại SHA-256 từng file trên NAS, so với catalog.
- `GET /health` → tình trạng service, kết nối Oracle, NAS.

## Non-functional

- Dump: ColdBoy tự đọc Oracle (JDBC) theo lịch hằng ngày, không nhận dữ liệu đẩy vào.
- Mỗi ngày (`CREATED_AT`) là một archive, ghi một lần, đủ 73 cột, giữ nguyên giá trị (số, thời gian, `NULL`).
- Single writer: một tiến trình ghi một archive; không ghi đồng thời, không cập nhật, không xoá bản ghi.
- Ghi ra ổ cục bộ, `fsync`, rồi chuyển lên NAS dưới tên tạm và đổi tên khi xong; lỗi giữa chừng thì không để lại archive dở.
- Toàn vẹn: từng file có SHA-256 (định dạng `sha256sum`, lưu cạnh archive và trong catalog), kiểm lại sau khi copy lên NAS.
- Mỗi lần đọc kiểm checksum phần dữ liệu đã đọc; hỏng thì báo lỗi, không trả dữ liệu sai.
- Không tự xoá dữ liệu ở Oracle; chủ nguồn xoá sau khi ngày ở trạng thái `archived` và đã verify.
- Chỉ một truy vấn: `REFERENCE` + `CREATED_AT` → bản ghi; không lọc cột khác, không đếm, không thống kê.
- Tài nguyên: pod tối đa 4 CPU / 4 GiB cho cả dump lẫn tra cứu; ưu tiên RAM thấp > dung lượng thấp > thời gian thấp.
- Quy mô: ~10 triệu dòng/ngày.
- Hiệu năng: tra 1 key dưới 1 giây; thời gian tra không tăng đáng kể theo kích thước file ngày; dump xong trong cửa sổ đêm.
- Định dạng và thư viện chuẩn, đọc được bằng công cụ chuẩn; cấm tự chế định dạng hoặc index/trailer riêng; pin phiên bản.
- NAS: sau khi hoàn tất, archive không bị ghi thêm (log, lock để ở ổ cục bộ).
- Nhiều bản ColdBoy đọc đồng thời; chỉ một job dump.
- Bảo mật: TLS, xác thực client, API admin tách quyền, log truy vấn; kết nối Oracle mã hoá.
- Ổn định, prod ready, thân thiện với dev; ngôn ngữ không bắt buộc.

## Chưa xác định

- Giao thức và băng thông NAS.
- Kích thước dòng thật (`avg_row_len`) và mức lặp của dữ liệu thật.
- `REFERENCE` có duy nhất trong một ngày không.
- Thời hạn lưu trữ.
