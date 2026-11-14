"""Sinh bộ dữ liệu golden cho test đúng/sai của mọi phương án.

Tạo ra hai file từ cùng một định nghĩa:
  golden.sql   — tạo bảng ARCHIVE_GOLDEN trong Oracle và nạp dữ liệu
  golden.jsonl — kết quả FIND mong đợi, đúng định dạng chuẩn (xem README, mục "Định dạng kết quả")

Chạy lại sau khi sửa file này:  python3 testdata/golden.py
"""

import json
import random
from pathlib import Path

TEXT_COLUMNS = 70
ROWS = 300
HERE = Path(__file__).parent

# Các giá trị khó: ký tự cần escape trong JSON, ký tự điều khiển, tiếng Việt, emoji 4 byte,
# U+2028, ký tự HTML, độ dài tối đa VARCHAR2(1000).
EDGE_TEXTS = [
    'nháy kép " và gạch chéo \\',
    "xuống dòng\nvà\r\ntab\tcuối",
    "điều khiển \x01\x08\x0c\x1f và DEL \x7f",
    "Giao dịch chuyển khoản số 123 — Ngân hàng",
    "emoji 💸🏦 4 byte",
    "line separator   paragraph  ",
    "<html> & 'nháy đơn'",
    "x" * 1000,
    "  khoảng trắng đầu và cuối  ",
]

# (id, reference, created_at) đặc biệt: ID lớn nhất của int64, mốc trước 1970, năm 9999,
# và reference mà thứ tự byte khác thứ tự ngôn ngữ ('B' < 'a').
EDGE_KEYS = [
    (9223372036854775807, "REF-B-MAX-ID", "2026-11-14 10:20:30.000001"),
    (1, "REF-a-ONE", "1969-12-31 23:59:59.999999"),
    (2, "REF-Z-OLD", "1900-01-01 00:00:00.000000"),
    (3, "REF-b-FUTURE", "9999-12-31 23:59:59.999999"),
    (4, "REF-0-ALL-NULL", "2026-01-01 00:00:00.000000"),
    (5, "REF-_-EMPTY", "2026-01-01 12:00:00.500000"),
]


def build_rows():
    rnd = random.Random(20261114)
    rows = []
    for i, (row_id, reference, created_at) in enumerate(EDGE_KEYS):
        if reference == "REF-0-ALL-NULL":
            texts = [None] * TEXT_COLUMNS
        elif reference == "REF-_-EMPTY":
            texts = [""] * TEXT_COLUMNS  # Oracle lưu '' thành NULL
        else:
            texts = [EDGE_TEXTS[(i + c) % len(EDGE_TEXTS)] for c in range(TEXT_COLUMNS)]
        rows.append((row_id, reference, created_at, texts))

    for n in range(len(EDGE_KEYS), ROWS):
        micros = rnd.randrange(0, 10**6)
        # Phần lớn dòng cùng một ngày, để một file ngày có nhiều row group / lô / block.
        created_at = "2026-11-14 " if n % 10 else f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d} "
        created_at += \
            f"{rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}.{micros:06d}"
        texts = []
        for c in range(TEXT_COLUMNS):
            roll = rnd.random()
            if roll < 0.2:
                texts.append(None)
            elif roll < 0.3:
                texts.append(rnd.choice(EDGE_TEXTS))
            else:
                texts.append(f"r{n}c{c + 1}_" + "".join(rnd.choices("abcdefghij ", k=rnd.randint(1, 120))))
        rows.append((1000 + n, f"TXN{rnd.randrange(10**12):012d}", created_at, texts))

    references = [r[1] for r in rows]
    assert len(set(references)) == len(references), "reference phải duy nhất"
    rnd.shuffle(rows)  # chèn không theo thứ tự, để kiểm tra ORDER BY
    return rows


def sql_string(value):
    """Mọi chuỗi đi qua UNISTR, nên sqlplus không làm hỏng ký tự ngoài ASCII hay ký tự điều khiển."""
    if value is None or value == "":
        return "NULL" if value is None else "''"
    out = []
    for ch in value:
        code = ord(ch)
        if 0x20 <= code < 0x7F and ch not in "'\\&":
            out.append(ch)
        elif code > 0xFFFF:
            code -= 0x10000
            out.append(f"\\{0xD800 + (code >> 10):04X}\\{0xDC00 + (code & 0x3FF):04X}")
        else:
            out.append(f"\\{code:04X}")
    return f"UNISTR('{''.join(out)}')"


def write_sql(rows):
    text_names = [f"TEXT_{c:02d}" for c in range(1, TEXT_COLUMNS + 1)]
    lines = [
        "-- Sinh bởi testdata/golden.py, không sửa tay.",
        "WHENEVER SQLERROR EXIT FAILURE",
        "SET DEFINE OFF",
        "SET FEEDBACK OFF",
        "BEGIN EXECUTE IMMEDIATE 'DROP TABLE ARCHIVE_GOLDEN'; EXCEPTION WHEN OTHERS THEN NULL; END;",
        "/",
        "CREATE TABLE ARCHIVE_GOLDEN (",
        "  ID NUMBER(19) NOT NULL, REFERENCE VARCHAR2(64) NOT NULL, CREATED_AT TIMESTAMP NOT NULL,",
        *[f"  {t} VARCHAR2(1000)," for t in text_names],
        "  CONSTRAINT PK_ARCHIVE_GOLDEN PRIMARY KEY (ID));",
    ]
    # sqlplus giới hạn độ dài một dòng, nên mỗi cột và mỗi giá trị nằm trên một dòng riêng.
    columns = ",\n  ".join(["ID", "REFERENCE", "CREATED_AT", *text_names])
    for row_id, reference, created_at, texts in rows:
        values = [str(row_id), sql_string(reference), f"TIMESTAMP '{created_at}'"]
        values += [sql_string(t) for t in texts]
        lines.append(f"INSERT INTO ARCHIVE_GOLDEN (\n  {columns})\nVALUES (\n  " + ",\n  ".join(values) + ");")
    lines += ["COMMIT;", "EXIT"]
    (HERE / "golden.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_jsonl(rows):
    out = []
    for row_id, reference, created_at, texts in sorted(rows, key=lambda r: r[1].encode()):
        record = {"id": row_id, "reference": reference, "created_at": created_at}
        for c, text in enumerate(texts, start=1):
            record[f"text_{c:02d}"] = text if text else None
        out.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    (HERE / "golden.jsonl").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    golden = build_rows()
    write_sql(golden)
    write_jsonl(golden)
    print(f"{len(golden)} dòng → testdata/golden.sql, testdata/golden.jsonl")
