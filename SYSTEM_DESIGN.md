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

## Ước lượng

### Giả định

- 10M dòng/ngày.
- Một dòng ~21 KB trước nén (như dữ liệu bench; dữ liệu thật nhân theo `avg_row_len / 21 KB`).
- Sau nén đo được: ~6.0 KB/dòng với dữ liệu lặp nhiều, ~7.4 KB/dòng với dữ liệu ngẫu nhiên.

### 1 ngày

- Dữ liệu lặp nhiều: 10M × 6.0 KB ≈ **60 GB**.
- Dữ liệu ngẫu nhiên: 10M × 7.4 KB ≈ **74 GB**.

### 1 tháng (30 ngày)

- Dữ liệu lặp nhiều: 30 × 60 GB ≈ **1.8 TB**.
- Dữ liệu ngẫu nhiên: 30 × 74 GB ≈ **2.2 TB**.

### 1 năm (365 ngày)

- Dữ liệu lặp nhiều: 365 × 60 GB ≈ **22 TB**.
- Dữ liệu ngẫu nhiên: 365 × 74 GB ≈ **27 TB**.

## Database design

ColdBoy có hai nơi lưu:

- **Catalog**: DB quan hệ nhỏ, riêng của ColdBoy (PostgreSQL), giữ trạng thái ngày, file, hash, job và log.
  Không dùng Oracle nguồn, vì tra cứu phải chạy được cả khi nguồn bảo trì hoặc đã xoá dữ liệu.
- **Archive trên NAS**: dữ liệu bản ghi. Mỗi ngày tự mô tả đủ (manifest + `SHA256SUMS`), nên catalog dựng lại
  được từ NAS nếu mất.

### Catalog

```sql
-- Một dòng mỗi ngày đã/đang archive.
CREATE TABLE archive_day (
    day            DATE        PRIMARY KEY,               -- ngày của CREATED_AT
    status         TEXT        NOT NULL CHECK (status IN ('pending', 'dumping', 'archived', 'failed')),
    row_count      BIGINT,                                -- số dòng đã ghi, so với COUNT(*) ở Oracle
    total_bytes    BIGINT,
    nas_path       TEXT,                                  -- thư mục của ngày trên NAS
    format_version INT         NOT NULL,                  -- đổi khi đổi định dạng / cấu trúc bản ghi
    source_query   TEXT        NOT NULL,                  -- câu SELECT đã dùng
    archived_at    TIMESTAMPTZ,
    verified_at    TIMESTAMPTZ,                           -- lần verify gần nhất thành công
    error          TEXT
);

-- Mỗi file của ngày và SHA-256 của nó.
CREATE TABLE archive_file (
    day        DATE     NOT NULL REFERENCES archive_day (day),
    path       TEXT     NOT NULL,                         -- đường dẫn tương đối trong thư mục ngày
    size_bytes BIGINT   NOT NULL,
    sha256     CHAR(64) NOT NULL,
    PRIMARY KEY (day, path)
);

-- Mỗi lần dump (theo lịch hoặc admin gọi lại).
CREATE TABLE dump_job (
    id           BIGSERIAL   PRIMARY KEY,
    day          DATE        NOT NULL,
    trigger      TEXT        NOT NULL CHECK (trigger IN ('schedule', 'admin')),
    requested_by TEXT,                                    -- client admin, NULL nếu theo lịch
    host         TEXT        NOT NULL,                    -- pod chạy job
    status       TEXT        NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ,
    row_count    BIGINT,
    total_bytes  BIGINT,
    error        TEXT
);
-- Single writer: tối đa một job đang chạy trên toàn hệ thống.
CREATE UNIQUE INDEX dump_job_one_running ON dump_job ((true)) WHERE status = 'running';

-- Mỗi lần verify (sau khi copy và khi admin gọi).
CREATE TABLE verify_run (
    id          BIGSERIAL   PRIMARY KEY,
    day         DATE        NOT NULL REFERENCES archive_day (day),
    trigger     TEXT        NOT NULL CHECK (trigger IN ('after_copy', 'admin')),
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    ok          BOOLEAN,
    bad_files   TEXT[]                                    -- file sai hash hoặc thiếu
);

-- Log truy vấn và thao tác admin.
CREATE TABLE audit_log (
    id         BIGSERIAL   PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    client_id  TEXT        NOT NULL,
    action     TEXT        NOT NULL,                      -- get, lookup, dump, verify
    day        DATE,
    key_count  INT,
    found      INT,
    status     INT         NOT NULL,                      -- HTTP status
    latency_ms INT
);
CREATE INDEX audit_log_at ON audit_log (at);
```

- Vòng đời một ngày: `pending` → `dumping` → `archived`, lỗi thì `failed`.
- `archived` chỉ đặt sau khi copy lên NAS, đổi tên xong và verify lần đầu đúng; ghi `archive_file` trong cùng
  transaction.
- Dump lại một ngày ghi ra thư mục mới, verify xong mới đổi `nas_path`, sau đó mới xoá thư mục cũ.
- Dung lượng catalog: ~365 dòng `archive_day` và vài nghìn dòng `archive_file` mỗi năm. `audit_log` là bảng lớn
  nhất, cần chia partition theo tháng và đặt thời hạn giữ.

### Archive trên NAS

```
/coldboy/<source_table>/<YYYY>/<MM>/<YYYY-MM-DD>/
    <các file dữ liệu>
    manifest.json      -- day, row_count, format_version, source_query, columns (tên, kiểu Oracle), archived_at
    SHA256SUMS         -- định dạng sha256sum, mọi file trừ chính nó
```

Bản ghi trong archive:

- **Key**: `REFERENCE`. Nếu `REFERENCE` không duy nhất trong ngày thì key là `REFERENCE` + `CREATED_AT` + `ID`,
  tra theo tiền tố `REFERENCE`.
- **Value**: đủ 73 cột, giữ kiểu: `NUMBER` ghi dạng chuỗi số chính xác, thời gian đủ phần lẻ giây, `NULL` là
  `null`. Tên và kiểu cột lấy từ metadata của câu SELECT, ghi vào `manifest.json`.
