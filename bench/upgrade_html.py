"""Vẽ upgrade.html ở thư mục gốc: 3 phương án ở cấu hình tốt nhất hiện có, sau thử nghiệm nén RocksDB
(ZSTD dictionary, block to hơn, mức nén cao hơn) trên go-rocksdb, dữ liệu lặp, một ngày 1M dòng.
Thiếu số thì ghi 'đang đo'. final.html (final_html.py) giữ nguyên.

Chạy qua `make summary` hoặc thẳng `python3 bench/upgrade_html.py`.
"""
import json
import re
from pathlib import Path

import final_html as fh  # dùng lại định dạng số và CSS

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "upgrade.html"
DS = "status"
OPS = ["dump", "find-1", "find-1000", "find-unknown"]

# Ba phương án ở cấu hình tốt nhất: (thư mục kết quả, tên, so với final.html, cấu hình)
BEST = [
    ("java-rocksdb-fair-on", "java-rocksdb", "Bật ZSTD dictionary",
     "Block 64 KB, ZSTD mức 3, dictionary 16 KB, train 2 MB, buffer 16 MB (đặt qua parser options native của RocksDB)"),
    ("go-rocksdb-fair-on", "go-rocksdb", "Bật ZSTD dictionary",
     "Block 64 KB, ZSTD mức 3, dictionary 16 KB, train 2 MB, buffer 16 MB"),
    ("java-parquet-duckdb-probe", "java-parquet-duckdb", "Không đổi cấu hình, đo lại",
     "Row group 128 MB, dictionary encoding, ZSTD; đo lại 16/11 trên file dump mới"),
]
# Thử nghiệm nén trên go-rocksdb: (thư mục, tên, block, dictionary, mức ZSTD)
TRIALS = [
    ("go-rocksdb-single", "Cấu hình cũ", "64 KB", "—", "mặc định"),
    ("go-rocksdb-dict", "+ dictionary", "64 KB", "64 KB", "mặc định"),
    ("go-rocksdb-b256-dict", "+ dictionary, block 256 KB", "256 KB", "64 KB", "mặc định"),
    ("go-rocksdb-b256-dict-l9", "+ dictionary, block 256 KB, mức 9", "256 KB", "64 KB", "9"),
]


def load(d, op):
    p = ROOT / "results" / DS / d / f"{op}.json"
    if not p.exists():
        return None
    s = json.loads(p.read_text())["summary"]
    return s if s.get("ok") else None


PARQUET_SIZES = [("java-parquet-duckdb-probe", "1M dòng"), ("java-parquet-duckdb-probe-2m", "2M dòng")]
# Ma trận fair theo đề xuất của sếp: cùng dữ liệu, cùng đường ghi (SstFileWriter + ingest), cùng tham số nén.
FAIR = [("java-rocksdb-fair-off", "Java OFF"), ("java-rocksdb-fair-on", "Java ON"),
        ("go-rocksdb-fair-off", "Go OFF"), ("go-rocksdb-fair-on", "Go ON")]
DIRS = {d for d, *_ in BEST} | {d for d, *_ in TRIALS} | {d for d, _ in PARQUET_SIZES} | {d for d, _ in FAIR} | {"java-rocksdb-single"}


def sst_check(d):
    """Đọc results/<d>/sst_check.txt (script bench ghi từ sst_dump): tham số nén native và khối dictionary."""
    p = ROOT / "results" / DS / d / "sst_check.txt"
    if not p.exists():
        return None, None
    t = p.read_text()
    m = re.search(r"max_dict_bytes=(\d+);.*?zstd_max_train_bytes=(\d+);.*?max_dict_buffer_bytes=(\d+);", t)
    level = re.search(r"level=(\d+);", t)
    opts = None if not m else (f"level={level.group(1) if level else '?'}<br>dict={int(m.group(1)) // 1024} KB<br>"
                               f"train={int(m.group(2)) / 2**20:g} MB<br>buffer={int(m.group(3)) / 2**20:g} MB")
    counts = [int(x) for x in re.findall(r"compression_dict: (\d+)", t)]
    return opts, (None if not counts else ("Có" if all(c > 0 for c in counts) else "Không"))
DATA = {(d, op): load(d, op) for d in DIRS for op in OPS}
# Thử lô đọc Oracle của Java: chỉ đo dump (lô không ảnh hưởng tra cứu).
FETCH = [("java-rocksdb-fair-on", "Java, ojdbc lô 10.000"), ("java-rocksdb-fair-on-f1k", "Java, ojdbc lô 1.000"),
         ("go-rocksdb-fair-on", "Go, godror lô 1.000")]
DATA.update({(d, "dump"): load(d, "dump") for d, _ in FETCH})


def get(d, op, key="wall_s"):
    s = DATA[(d, op)]
    return None if s is None else s.get(key)


def pct(a, b):
    return fh.PENDING if a is None or b is None else f"{(a / b - 1) * 100:+.1f}%".replace(".", ",")


def metric_rows(dirs):
    v = lambda op, k: [get(d, op, k) for d in dirs]
    rows = [
        fh.row("Dung lượng file", v("dump", "file_mb"), fh.fmt_gb),
        fh.row("Tra 1 key · thời gian", v("find-1", "wall_s"), fh.fmt_s, "gồm khởi động tiến trình"),
        fh.row("Tra 1000 key · thời gian", v("find-1000", "wall_s"), fh.fmt_s),
        fh.row("Tra 1000 key · RAM đỉnh", v("find-1000", "peak_rss_mb"), fh.fmt_mb),
        fh.row("Tra key không tồn tại", v("find-unknown", "wall_s"), fh.fmt_s),
        fh.row("Dump · thời gian", v("dump", "wall_s"), fh.fmt_s),
        fh.row("Dump · RAM đỉnh", v("dump", "peak_rss_mb"), fh.fmt_mb),
    ]
    checks = [fh.PENDING if DATA[(d, "find-1000")] is None else
              f'{DATA[(d, "find-1000")]["matched"]}/{DATA[(d, "find-1000")]["checked"]}' for d in dirs]
    rows.append(f'<tr><th scope=row>Khớp Oracle từng byte</th>{"".join(f"<td class=num>{x}</td>" for x in checks)}</tr>')
    return rows


def best_table():
    head = "".join(f'<th class="num">{name}<small>{change}</small></th>' for _, name, change, _ in BEST)
    conf = "".join(f"<td>{c}</td>" for *_, c in BEST)
    rows = [f"<tr><th scope=row>Cấu hình</th>{conf}</tr>"] + metric_rows([d for d, *_ in BEST])
    return (f'<div class="scroll"><table><thead><tr><th>Dữ liệu lặp, 1M dòng</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def fair_table():
    dirs = [d for d, _ in FAIR]
    head = "".join(f'<th class="num">{label}</th>' for _, label in FAIR)
    rows = metric_rows(dirs)
    cpu = [get(d, "dump", "cpu_s") for d in dirs]
    rows.insert(5, fh.row("Dump · CPU", cpu, fh.fmt_s))
    checks = [sst_check(d) for d in dirs]
    rows.append("<tr><th scope=row>Tham số native trong SST<small>sst_dump --show_properties</small></th>"
                + "".join(f"<td class=num>{o or fh.PENDING}</td>" for o, _ in checks) + "</tr>")
    rows.append("<tr><th scope=row>File có dictionary<small>khối rocksdb.compression_dict</small></th>"
                + "".join(f"<td class=num>{c or fh.PENDING}</td>" for _, c in checks) + "</tr>")
    return (f'<div class="scroll"><table><thead><tr><th>Dữ liệu lặp, 1M dòng</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def fetch_table():
    dirs = [d for d, _ in FETCH]
    head = "".join(f'<th class="num">{label}</th>' for _, label in FETCH)
    v = lambda k: [get(d, "dump", k) for d in dirs]
    rows = [fh.row("Dump · thời gian", v("wall_s"), fh.fmt_s), fh.row("Dump · CPU", v("cpu_s"), fh.fmt_s),
            fh.row("Dump · RAM đỉnh", v("peak_rss_mb"), fh.fmt_mb), fh.row("Dung lượng file", v("file_mb"), fh.fmt_gb)]
    return (f'<div class="scroll"><table><thead><tr><th>Dictionary ON, 1M dòng</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def trial_table():
    dirs = [d for d, *_ in TRIALS]
    head = "".join(f'<th class="num">{name}<small>block {b} · dict {dic} · mức {lvl}</small></th>'
                   for _, name, b, dic, lvl in TRIALS)
    base = get("go-rocksdb-single", "dump", "file_mb")
    ratio = "".join(f"<td class=num>{pct(get(d, 'dump', 'file_mb'), base)}</td>" for d in dirs)
    rows = metric_rows(dirs)
    rows.insert(1, f"<tr><th scope=row>So với cấu hình cũ</th>{ratio}</tr>")
    return (f'<div class="scroll"><table><thead><tr><th>go-rocksdb, dữ liệu lặp, 1M</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def parquet_table():
    head = "".join(f'<th class="num">{label}</th>' for _, label in PARQUET_SIZES)

    def r(label, op, grows):
        vals = "".join(f"<td class=num>{fh.fmt_s(get(d, op))}</td>" for d, _ in PARQUET_SIZES)
        return f"<tr><th scope=row>{label}</th>{vals}<td>{grows}</td></tr>"
    return (f'<div class="scroll"><table><thead><tr><th>java-parquet-duckdb, dữ liệu lặp</th>{head}'
            f'<th>Tăng theo kích thước file?</th></tr></thead><tbody>'
            f'{r("Tra 1 key", "find-1", "Không")}{r("Tra key không tồn tại", "find-unknown", "Không")}'
            f'{r("Tra 1000 key", "find-1000", "<b>Có</b>, gấp đôi khi file gấp đôi")}</tbody></table></div>')


def verdict():
    jr, gr, pq = (get(d, "dump", "file_mb") for d, *_ in BEST)
    joff = get("java-rocksdb-fair-off", "dump", "file_mb")
    goff = get("go-rocksdb-fair-off", "dump", "file_mb")
    g64 = get("go-rocksdb-dict", "dump", "file_mb")
    old = get("go-rocksdb-single", "dump", "file_mb")
    if None in (jr, gr, pq, joff, goff, g64, old):
        return ""
    t = lambda d, op: fh.fmt_s(get(d, op))
    return f"""<section class="verdict">
  <p><b>Java và Go bật dictionary như nhau</b> (đo fair, cùng tham số): file OFF {fh.fmt_gb(joff)} = {fh.fmt_gb(goff)},
  ON {fh.fmt_gb(jr)} = {fh.fmt_gb(gr)} ({pct(gr, goff)}). sst_dump xác nhận cả hai ghi cùng tham số native và có
  khối dictionary. Nhận định trước đây rằng Java không bật được dictionary là sai.</p>
  <p><b>Tra cứu gần như không đổi khi bật</b>: 1000 key Java {t("java-rocksdb-fair-off", "find-1000")} →
  {t("java-rocksdb-fair-on", "find-1000")}, Go {t("go-rocksdb-fair-off", "find-1000")} →
  {t("go-rocksdb-fair-on", "find-1000")}. Cái giá là dump chậm hơn (Java {t("java-rocksdb-fair-off", "dump")} →
  {t("java-rocksdb-fair-on", "dump")}, Go {t("go-rocksdb-fair-off", "dump")} → {t("go-rocksdb-fair-on", "dump")}).</p>
  <p><b>Khác biệt Java–Go không nằm ở nén</b> mà ở tốc độ và RAM: Go tra 1000 key nhanh hơn ~2 lần, dump nhanh hơn
  ~1,5 lần, RAM dump ~3 lần ít hơn.</p>
  <p><b>Dictionary 64 KB nén thêm được</b>: trên go-rocksdb file {fh.fmt_gb(g64)} ({pct(g64, old)}) so với
  {pct(gr, goff)} của 16 KB, tra không chậm hơn. java-parquet-duckdb không đổi cấu hình: file {fh.fmt_gb(pq)}, tra
  1000 key {t("java-parquet-duckdb-probe", "find-1000")}.</p>
</section>"""


STYLE = re.search(r"<style>.*?</style>", fh.HTML, re.S).group(0)
pending = sum(1 for s in DATA.values() if s is None)
pending_note = "" if not pending else (
    f'<p class="callout">Còn {pending} ô <span class="pending">đang đo</span>; chạy <code>make summary</code> sau khi bench xong.</p>')

HTML = f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Archive ColdBoy nâng cấp</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
{STYLE}
</head>
<body>
<main>
<header>
  <span class="eyebrow">ColdBoy · cập nhật sau báo cáo final · 16/11/2026</span>
  <h1>Ba phương án ở cấu hình tốt nhất hiện có</h1>
  <p class="lead">Sau báo cáo final, hai phương án RocksDB bật thêm ZSTD dictionary của RocksDB để nén tốt hơn.
  java-parquet-duckdb giữ cấu hình. Đo trên dữ liệu lặp, một ngày 1M dòng.</p>
</header>
{pending_note}
{verdict()}

<section>
  <h2>So sánh 3 phương án</h2>
  <p>Ô tô xanh là tốt nhất trong hàng. Thời gian là trung vị, RAM là đỉnh (RSS cả tiến trình), tra cứu có 1 lần làm
  nóng rồi đo 3 lần. Mỗi bản ghi trả về được so từng byte với Oracle.</p>
  {best_table()}
  <p class="note">Tra cứu bản Java gồm khoảng 0,4 s khởi động JVM; java-parquet-duckdb thêm khoảng 1 s mở DuckDB
  (giải nén thư viện native 64 MB). Trong service chạy lâu hai khoản này chỉ trả một lần.</p>
</section>

<section>
  <h2>Best practice theo từng phương án</h2>
  <div class="scroll"><table class="plain"><thead><tr><th>Phương án</th><th>Nên dùng</th><th>Không nên</th></tr></thead><tbody>
  <tr><th scope=row>go-rocksdb</th><td>Bật ZSTD dictionary (<code>ROCKSDB_ZSTD_DICT_BYTES=65536</code>), giữ block 64 KB và
  mức ZSTD mặc định; index và bloom phân vùng; ghi bằng <code>SstFileWriter</code> + ingest; đọc Oracle bằng godror lô
  1.000, tắt prefetch.</td>
  <td>Block 256 KB, mức ZSTD 9: không nén thêm, tra hoặc dump chậm hơn. Driver go-ora: ~1,2 GB RAM ở lô 10.000.</td></tr>
  <tr><th scope=row>java-rocksdb</th><td>Bật ZSTD dictionary như bản Go, đặt qua parser options native
  (<code>ColumnFamilyOptions.getColumnFamilyOptionsFromProps</code>, chế độ nghiêm ngặt) cho cả
  <code>compression_opts</code> và <code>bottommost_compression_opts</code>; block 64 KB, index và bloom phân vùng;
  ojdbc lô 10.000.</td>
  <td>Đặt dictionary qua <code>CompressionOptions</code> của rocksdbjni: lớp này thiếu <code>max_dict_buffer_bytes</code>,
  RocksDB sẽ giữ cả file trong RAM để lấy mẫu. Gọi lại <code>setCompressionOptions</code> sau khi parse: ghi đè mất giới hạn.</td></tr>
  <tr><th scope=row>java-parquet-duckdb</th><td>Ghi dữ liệu đã sort theo <code>REFERENCE</code> (đang làm, nhờ đó mỗi key chỉ
  đọc 1 row group); chạy thành service thường trực để không trả ~1,4 s khởi động JVM và DuckDB ở mỗi lần tra; gom
  key tra theo lô vào một câu <code>IN (...)</code>.</td>
  <td>Bloom filter và page index (đã thử, phương án parquet-lookup): file to hơn, tra không nhanh hơn. Kỳ vọng tra nhanh
  như RocksDB: không đạt được (mục dưới).</td></tr>
  </tbody></table></div>
</section>

<section>
  <h2>Vì sao Parquet tra chậm, và không sửa triệt để được</h2>
  {parquet_table()}
  <ul>
    <li>Parquet lưu theo cột, nén theo row group (~21k dòng, ~117 MB). Lấy 1 bản ghi đủ 73 cột phải giải nén nguyên row
    group chứa nó.</li>
    <li><b>Tra 1 key không tăng theo file</b>: dữ liệu đã sort nên min/max của từng row group chỉ ra đúng 1 row group
    (đo được: quét 1/48 ở 1M, 1/96 ở 2M). Thời gian gồm ~0,4 s khởi động JVM, ~1 s mở DuckDB, ~0,2 s truy vấn.</li>
    <li><b>Tra 1000 key tăng theo file</b>: 1000 key ngẫu nhiên chạm gần hết row group, nên đọc gần cả file; file gấp đôi
    thì phải giải nén gấp đôi.</li>
    <li>RocksDB lưu theo dòng, đơn vị nén là block 64 KB (~3 bản ghi): 1000 key chỉ đọc ~1000 block nhỏ, không phụ
    thuộc kích thước file.</li>
    <li><b>Không sửa triệt để được</b> vì đây là bản chất của định dạng theo cột. Row group nhỏ hơn giảm byte mỗi lần
    tra nhưng footer (metadata mọi row group) to ra, làm chính tra 1 key tăng theo file, và nén kém đi. Service thường
    trực bỏ được phần khởi động, mỗi key vẫn ~0,2 s so với vài ms của RocksDB.</li>
  </ul>
</section>

<section>
  <h2>ZSTD dictionary là gì</h2>
  <ul>
    <li>RocksDB lưu theo dòng, nén từng block 64 KB (~3 bản ghi) độc lập. Mỗi block bắt đầu với "trí nhớ" rỗng nên
    các chuỗi lặp khắp file ("khoản", <code>BRANCH_00</code>, tên cột JSON…) phải ghi lại ở từng block. Parquet nén
    cả cột của ~21k dòng một lần nên không bị như vậy.</li>
    <li>Bật dictionary: lúc ghi, RocksDB lấy mẫu dữ liệu (tối đa 64 MB), train một từ điển 64 KB, lưu một lần trong
    file; mọi block nén và giải nén dựa trên từ điển đó. Block vẫn nhỏ nên tra vẫn nhanh.</li>
    <li>Là tuỳ chọn chuẩn của RocksDB (<code>max_dict_bytes</code>, <code>zstd_max_train_bytes</code>,
    <code>max_dict_buffer_bytes</code>); file đọc được bằng RocksDB thông thường.</li>
  </ul>
</section>

<section>
  <h2>So sánh fair Java và Go, dictionary OFF / ON</h2>
  <p>Theo đề xuất của sếp: cùng dữ liệu 1M dòng, cùng key và value JSON, cùng đường ghi (<code>SstFileWriter</code> rồi
  ingest, một writer), cùng tham số nén đặt cho cả <code>compression_opts</code> và <code>bottommost_compression_opts</code>:</p>
  <ul>
    <li><b>OFF</b>: ZSTD mức 3, không dictionary.</li>
    <li><b>ON</b>: ZSTD mức 3, dictionary 16 KB, mẫu train 2 MB, buffer lấy mẫu 16 MB, ZSTD trainer.</li>
  </ul>
  <p>Java đặt tham số qua parser options native (<code>getColumnFamilyOptionsFromProps</code>, chế độ nghiêm ngặt) vì
  lớp <code>CompressionOptions</code> của rocksdbjni không có <code>max_dict_buffer_bytes</code>. Mỗi ca dump 3 lần lấy
  trung vị; tra cứu 1 lần làm nóng rồi 3 lần. Sau mỗi dump, <code>sst_dump</code> xác nhận tham số đã xuống native và
  file có khối dictionary.</p>
  {fair_table()}
  <p class="note">Khác biệt còn lại giữa hai bên, không đồng nhất được: RocksDB native của Java là bản đóng gói trong
  rocksdbjni 11.8.1, của Go là librocksdb 11.13.2 (Homebrew); driver đọc Oracle là ojdbc lô 10.000 và godror lô 1.000;
  ngân sách RAM runtime <code>-Xmx2g</code> và <code>GOMEMLIMIT=2GiB</code>.</p>
  <h3>Java đọc Oracle lô 1.000 như Go</h3>
  <p>Thử bỏ khác biệt về lô đọc: chạy lại dump Java (dictionary ON) với <code>ORACLE_FETCH_SIZE=1000</code>, 3 lần lấy
  trung vị. RAM giảm phần bộ đệm của lô lớn, thời gian và CPU không đổi: lô đọc không phải lý do Java chậm hơn Go.
  Phần chênh còn lại nằm ở JVM (đổi dữ liệu sang <code>String</code>, ghi JSON, chép qua JNI, heap nền), chưa
  profile tách từng khoản.</p>
  {fetch_table()}
</section>

<section>
  <h2>Thử nghiệm trên go-rocksdb</h2>
  <p>Thử 3 tuỳ chọn nén chuẩn của RocksDB. Chỉ dictionary có lợi: block 256 KB và mức ZSTD 9 không làm file nhỏ thêm,
  trong khi tra 1000 key chậm hơn, RAM tra gấp đôi, mức 9 còn làm dump chậm gấp hơn 2 lần.</p>
  {trial_table()}
</section>

<section>
  <h2>Cách bật và giới hạn</h2>
  <ul>
    <li>Cả hai bản RocksDB: <code>ROCKSDB_ZSTD_DICT_BYTES=65536</code>. Tuỳ chọn thêm: <code>ROCKSDB_ZSTD_TRAIN_BYTES</code>
    (mặc định 100 × dict), <code>ROCKSDB_ZSTD_BUFFER_BYTES</code> (mặc định 64 MiB), <code>ROCKSDB_ZSTD_LEVEL</code>.
    Không đặt thì giữ cấu hình cũ.</li>
    <li>Đã xác nhận bằng <code>sst_dump --show_properties</code> trên file 100k dòng: Java và Go ghi cùng tham số native
    (<code>max_dict_bytes=65536; zstd_max_train_bytes=6553600; max_dict_buffer_bytes=67108864</code>), file SST có khối
    <code>rocksdb.compression_dict</code>; bản tắt dictionary không có khối này.</li>
    <li>Chưa thử bộ tham số nhỏ hơn (dictionary 16 KB, mẫu 2 MB, buffer 16 MB): RAM khi dump có thể thấp hơn, độ nén
    cần đo lại.</li>
    <li>Cái giá: dump chậm hơn khoảng 25% (go-rocksdb), RAM khi dump tăng do giữ mẫu để train. Phần mẫu có giới hạn
    cứng nên RAM không nên tăng theo số dòng; chưa đo ở 2M để xác nhận.</li>
    <li>Chỉ đo ở 1M dòng dữ liệu lặp; dữ liệu thật lặp nhiều hơn nên dictionary có thể còn lợi hơn.</li>
  </ul>
</section>

<footer>Sinh từ các file trong <code>results/status/</code> của repo parquet-profile. Cùng máy và cách đo với
<code>final.html</code>.</footer>
</main>
</body>
</html>
"""


def write():
    OUT.write_text(HTML, encoding="utf-8")
    return OUT, [f"{d} {op}" for (d, op), s in DATA.items() if s is None]


if __name__ == "__main__":
    path, missing = write()
    print(f"Đã vẽ {path.relative_to(ROOT)}" + (f" · còn {len(missing)} ô đang đo" if missing else ""))
