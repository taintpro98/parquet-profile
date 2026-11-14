# Quy tắc cho trợ lý AI trong repo này

## Git

- **CẤM** `git add` / stage, `git commit`, `git push` khi người dùng chưa ra lệnh rõ ràng cho đúng
  thao tác đó. Một lần được phép không áp dụng cho lần sau.
- Được phép dùng các lệnh chỉ đọc: `git status`, `git diff`, `git log`.
- `git mv` / `git rm` cũng là stage: dùng `mv` / `rm` thường.

## Bài toán (đọc trước khi thiết kế phương án)

- Truy vấn là **key (`REFERENCE`) + timestamp (`CREATED_AT`) → một bản ghi đầy đủ** (73 cột).
- Dữ liệu chia theo **ngày**: mỗi ngày khoảng 10 triệu dòng, lưu trong **một file** (hoặc một vùng file)
  đại diện cho ngày đó. Timestamp chọn file/vùng của ngày, key tìm bản ghi trong đó.
- Mọi phương án phải theo cách chia và cách tìm này; benchmark mô phỏng một ngày 10M dòng.
- Mục tiêu là so sánh các phương án **lưu và nén** dữ liệu (định dạng, codec), không bắt buộc dùng
  thư viện nào cụ thể.
