# Quy tắc cho trợ lý AI trong repo này

## Git

- **CẤM** `git add` / stage, `git commit`, `git push` khi người dùng chưa ra lệnh rõ ràng cho đúng
  thao tác đó. Một lần được phép không áp dụng cho lần sau.
- Được phép dùng các lệnh chỉ đọc: `git status`, `git diff`, `git log`.
- `git mv` / `git rm` cũng là stage: dùng `mv` / `rm` thường.

## Kết quả benchmark

- Mỗi lần chạy benchmark lưu kết quả thành file trong `results/`, để chạy riêng từng phương án / bộ
  dữ liệu / kịch bản mà không phải chạy lại tất cả.
- Một lệnh summarize riêng (`make summary`) đọc các file trong `results/` và vẽ `final-report.html` ở thư mục gốc
  (chỉ 3 candidate). `report.html` là báo cáo cũ của mọi phương án, giữ nguyên, không vẽ lại.
  Thiếu dữ liệu phần nào thì để trống phần đó, không báo lỗi.

## Bài toán (đọc trước khi thiết kế phương án)

- Truy vấn là **key (`REFERENCE`) + timestamp (`CREATED_AT`) → một bản ghi đầy đủ** (73 cột).
  Đây là truy vấn **duy nhất**: không lọc theo cột khác, không đếm, không thống kê/phân tích. Không
  đánh giá phương án theo khả năng SQL/BI/ad-hoc.
- Dữ liệu chia theo **ngày**: mỗi ngày khoảng 10 triệu dòng, lưu trong **một file** (hoặc một vùng file)
  đại diện cho ngày đó. Timestamp chọn file/vùng của ngày, key tìm bản ghi trong đó.
- Mọi phương án phải theo cách chia và cách tìm này; benchmark mô phỏng một ngày 10M dòng.
- Mỗi file ngày **ghi một lần** bởi một tiến trình, sau đó chỉ đọc. Không có ghi đồng thời, không
  cập nhật hay xoá bản ghi.
- Mục tiêu là so sánh các phương án **lưu và nén** dữ liệu (định dạng, codec), không bắt buộc dùng
  thư viện nào cụ thể.
- **Chỉ dùng định dạng và thư viện chuẩn.** Cấm tự chế định dạng file, kể cả nối thêm index/trailer
  riêng vào một định dạng chuẩn. File phải đọc được bằng thư viện/công cụ chuẩn của định dạng đó.
- Dữ liệu thật **lặp nhiều** (trạng thái, kênh, loại giao dịch… trùng giữa các dòng), không ngẫu nhiên
  hoàn toàn. Dữ liệu seed phải phản ánh điều này, nếu không sẽ đánh giá sai độ nén của từng định dạng.
