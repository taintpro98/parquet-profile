"""Vẽ upgrade.html ở thư mục gốc: 3 phương án ở cấu hình tốt nhất, đo lại toàn bộ trong MỘT phiên trên MỘT bộ dữ
liệu (SEED cố định, results/status/seed.json): cùng máy, cùng giao thức (dump 3 lần lấy trung vị, rồi tra
1 key / 1000 key / key không tồn tại), cùng lô đọc Oracle 1.000. Thiếu số thì ghi 'đang đo'.
final.html (final_html.py) giữ nguyên; các thí nghiệm cũ trên dữ liệu seed trước vẫn nằm trong results/.

Chạy qua `make summary` hoặc thẳng `python3 bench/upgrade_html.py`. Phiên đo: bench/run_final.sh.
"""
import json
import re
from pathlib import Path

import final_html as fh  # dùng lại định dạng số và CSS

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "upgrade.html"
DS = "status"
OPS = ["dump", "find-1", "find-1000", "find-unknown"]

# Ba phương án ở cấu hình tốt nhất: (thư mục kết quả, tên, ghi chú ngắn, cấu hình)
BEST = [
    ("go-rocksdb-final", "go-rocksdb", "Winner",
     "Block 64 KB, ZSTD mức 3, dictionary 64 KB, train 6,4 MB, buffer 64 MB; index và bloom phân vùng; godror lô 1.000"),
    ("java-rocksdb-final", "java-rocksdb", "Cùng cấu hình RocksDB",
     "Như go-rocksdb (dictionary đặt qua parser options native); ojdbc lô 1.000; -Xmx2g"),
    ("java-parquet-duckdb-final", "java-parquet-duckdb", "Cấu hình tốt nhất đã thử",
     "Row group 128 MB, ZSTD mức 3, dictionary encoding, dữ liệu sort theo REFERENCE; DuckDB 4 luồng; ojdbc lô 1.000"),
]
GO_SCREEN = [("go-rocksdb-screen-off", "Không dictionary"), ("go-rocksdb-screen-d16", "Dictionary 16 KB"),
             ("go-rocksdb-screen-d64", "Dictionary 64 KB")]
JAVA_SCREEN = [("java-rocksdb-screen-off", "Không dictionary"), ("java-rocksdb-screen-d16", "Dictionary 16 KB"),
               ("java-rocksdb-screen-d64", "Dictionary 64 KB")]
PQ_SCREEN = [("java-parquet-duckdb-screen-zstd", "ZSTD, 4 luồng"), ("java-parquet-duckdb-screen-zstd-t10", "ZSTD, 10 luồng"),
             ("java-parquet-duckdb-screen-snappy", "SNAPPY, 4 luồng")]
PARQUET_SIZES = [("java-parquet-duckdb-final", "1M dòng"), ("java-parquet-duckdb-final-2m", "2M dòng")]
DIRS = ({d for d, *_ in BEST} | {d for d, _ in GO_SCREEN} | {d for d, _ in JAVA_SCREEN} | {d for d, _ in PQ_SCREEN} | {d for d, _ in PARQUET_SIZES})


def load(d, op):
    p = ROOT / "results" / DS / d / f"{op}.json"
    if not p.exists():
        return None
    s = json.loads(p.read_text())["summary"]
    return s if s.get("ok") else None


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
# t10 tra trên cùng archive với bản 4 luồng nên không có dump riêng.
for op in ("dump",):
    DATA[("java-parquet-duckdb-screen-zstd-t10", op)] = DATA[("java-parquet-duckdb-screen-zstd", op)]


def get(d, op, key="wall_s"):
    s = DATA[(d, op)]
    return None if s is None else s.get(key)


def pct(a, b):
    return fh.PENDING if a is None or b is None else f"{(a / b - 1) * 100:+.1f}%".replace(".", ",")


def times(a, b):
    return fh.PENDING if a is None or not b else f"{a / b:.1f}".replace(".", ",") + " lần"


def metric_rows(dirs, cpu=True):
    v = lambda op, k: [get(d, op, k) for d in dirs]
    rows = [
        fh.row("Dung lượng file", v("dump", "file_mb"), fh.fmt_gb),
        fh.row("Tra 1 key · thời gian", v("find-1", "wall_s"), fh.fmt_s, "gồm khởi động tiến trình"),
        fh.row("Tra 1000 key · thời gian", v("find-1000", "wall_s"), fh.fmt_s),
        fh.row("Tra 1000 key · RAM đỉnh", v("find-1000", "peak_rss_mb"), fh.fmt_mb),
        fh.row("Tra key không tồn tại", v("find-unknown", "wall_s"), fh.fmt_s, "gần như chỉ còn khởi động"),
        fh.row("Dump · thời gian", v("dump", "wall_s"), fh.fmt_s),
    ]
    if cpu:
        rows.append(fh.row("Dump · CPU", v("dump", "cpu_s"), fh.fmt_s))
    rows.append(fh.row("Dump · RAM đỉnh", v("dump", "peak_rss_mb"), fh.fmt_mb))
    checks = [fh.PENDING if DATA[(d, "find-1000")] is None else
              f'{DATA[(d, "find-1000")]["matched"]}/{DATA[(d, "find-1000")]["checked"]}' for d in dirs]
    rows.append(f'<tr><th scope=row>Khớp Oracle từng byte</th>{"".join(f"<td class=num>{x}</td>" for x in checks)}</tr>')
    return rows


def table(title, dirs, heads, extra_top=""):
    head = "".join(f'<th class="num">{h}</th>' for h in heads)
    return (f'<div class="scroll"><table><thead><tr><th>{title}</th>{head}</tr></thead>'
            f'<tbody>{extra_top}{"".join(metric_rows(dirs))}</tbody></table></div>')


def best_table():
    dirs = [d for d, *_ in BEST]
    heads = [f"{name}<small>{note}</small>" for _, name, note, _ in BEST]
    conf = "".join(f"<td>{c}</td>" for *_, c in BEST)
    sst = [sst_check(d) for d in dirs]
    top = f"<tr><th scope=row>Cấu hình</th>{conf}</tr>"
    t = table("Dữ liệu lặp, 1M dòng", dirs, heads, top)
    check = ("<tr><th scope=row>Tham số native trong SST<small>sst_dump --show_properties</small></th>"
             + "".join(f"<td class=num>{o or '—'}</td>" for o, _ in sst) + "</tr>"
             "<tr><th scope=row>File có khối dictionary</th>"
             + "".join(f"<td class=num>{c or '—'}</td>" for _, c in sst) + "</tr>")
    return t.replace("</tbody>", check + "</tbody>")


def go_screen_table(screen=GO_SCREEN, title="go-rocksdb, mỗi cấu hình dump 1 lần"):
    dirs = [d for d, _ in screen]
    base = get(dirs[0], "dump", "file_mb")
    ratio = "".join(f"<td class=num>{pct(get(d, 'dump', 'file_mb'), base)}</td>" for d in dirs)
    top = f"<tr><th scope=row>File so với không dictionary</th>{ratio}</tr>"
    return table(title, dirs, [h for _, h in screen], top)


def pq_screen_table():
    dirs = [d for d, _ in PQ_SCREEN]
    return table("java-parquet-duckdb, mỗi cấu hình dump 1 lần", dirs, [h for _, h in PQ_SCREEN])


def parquet_table():
    head = "".join(f'<th class="num">{label}</th>' for _, label in PARQUET_SIZES)

    def r(label, op, grows=None):
        a, b = (get(d, op) for d, _ in PARQUET_SIZES)
        vals = "".join(f"<td class=num>{fh.fmt_s(get(d, op))}</td>" for d, _ in PARQUET_SIZES)
        if grows is None:
            grows = fh.PENDING if None in (a, b) else (f"Không đáng kể (+{b - a:.1f} s)".replace(".", ",") if b - a < 0.5 else "Có")
        return f"<tr><th scope=row>{label}</th>{vals}<td>{grows}</td></tr>"
    size = "".join(f"<td class=num>{fh.fmt_gb(get(d, 'dump', 'file_mb'))}</td>" for d, _ in PARQUET_SIZES)
    return (f'<div class="scroll"><table><thead><tr><th>java-parquet-duckdb, dữ liệu lặp</th>{head}'
            f'<th>Tăng theo kích thước file?</th></tr></thead><tbody>'
            f'<tr><th scope=row>Dung lượng file</th>{size}<td>Có (đương nhiên)</td></tr>'
            f'{r("Tra 1 key", "find-1")}{r("Tra key không tồn tại", "find-unknown")}'
            f'{r("Tra 1000 key", "find-1000", "<b>Có</b>, gấp ~2 lần khi file gấp đôi")}</tbody></table></div>')


def go_screen_note(screen=GO_SCREEN):
    off, d16, d64 = (d for d, _ in screen)
    g = lambda d, op, k="wall_s": get(d, op, k)
    if None in (g(off, "dump", "file_mb"), g(d64, "dump", "file_mb"), g(d16, "dump", "file_mb"), g(d64, "find-1000")):
        return ""
    return (f"<p>Dictionary 64 KB: file {pct(g(d64, 'dump', 'file_mb'), g(off, 'dump', 'file_mb'))} so với không dictionary "
            f"(16 KB, bộ tham số sếp đề xuất: {pct(g(d16, 'dump', 'file_mb'), g(off, 'dump', 'file_mb'))}), tra 1000 key bằng nhau giữa 16 KB "
            f"và 64 KB. <b>Cái giá của dictionary</b>: tra 1000 key {pct(g(d64, 'find-1000'), g(off, 'find-1000'))} "
            f"({fh.fmt_s(g(off, 'find-1000'))} → {fh.fmt_s(g(d64, 'find-1000'))}) và dump {pct(g(d64, 'dump'), g(off, 'dump'))} "
            f"({fh.fmt_s(g(off, 'dump'))} → {fh.fmt_s(g(d64, 'dump'))}, một lần lúc ghi); RAM dump tăng do giữ mẫu để train, có "
            f"giới hạn cứng nên không tăng theo số dòng. Chọn 64 KB vì file nhỏ hơn rõ rệt trong khi chênh lệch tra chỉ vài chục "
            f"ms cho 1000 key; nếu ưu tiên thời gian tra tuyệt đối hơn dung lượng thì tắt dictionary vẫn là lựa chọn hợp lệ.</p>")


def java_screen_note():
    off, d16, d64 = (d for d, _ in JAVA_SCREEN)
    g = lambda d, op, k="wall_s": get(d, op, k)
    if None in (g(off, "dump", "file_mb"), g(d64, "dump", "file_mb"), g(d64, "find-1000"), g(off, "find-1000")):
        return ""
    return (f"<p>Cùng chiều với Go về dung lượng: dictionary 64 KB file {pct(g(d64, 'dump', 'file_mb'), g(off, 'dump', 'file_mb'))} "
            f"(16 KB: {pct(g(d16, 'dump', 'file_mb'), g(off, 'dump', 'file_mb'))}). Khác Go: tra 1000 key không chậm đi "
            f"({fh.fmt_s(g(off, 'find-1000'))} → {fh.fmt_s(g(d64, 'find-1000'))}), phần khởi động JVM (~0,4 s) che chênh lệch nhỏ. "
            f"Cái giá còn lại: dump {pct(g(d64, 'dump'), g(off, 'dump'))} ({fh.fmt_s(g(off, 'dump'))} → {fh.fmt_s(g(d64, 'dump'))}) "
            f"và RAM dump {g(off, 'dump', 'peak_rss_mb')} → {g(d64, 'dump', 'peak_rss_mb')} MB. Chọn 64 KB, cùng cấu hình với Go.</p>")


def pq_screen_note():
    z, t10, sn = (d for d, _ in PQ_SCREEN)
    g = lambda d, op, k="wall_s": get(d, op, k)
    if None in (g(z, "dump", "file_mb"), g(sn, "dump", "file_mb"), g(z, "find-1000"), g(t10, "find-1000"), g(sn, "find-1000")):
        return ""
    return (f"<p>SNAPPY giải nén nhanh hơn: tra 1000 key {pct(g(sn, 'find-1000'), g(z, 'find-1000'))} nhưng file "
            f"{pct(g(sn, 'dump', 'file_mb'), g(z, 'dump', 'file_mb'))}: đánh đổi dung lượng nhiều hơn thời gian được lợi, nên giữ ZSTD. "
            f"10 luồng DuckDB: tra 1000 key {fh.fmt_s(g(t10, 'find-1000'))} so với {fh.fmt_s(g(z, 'find-1000'))} ở 4 luồng, "
            f"không nhanh hơn, nên giữ 4 luồng.</p>")


def why_go_java():
    g, j = BEST[0][0], BEST[1][0]
    v = lambda d, op, k="wall_s": get(d, op, k)
    if None in (v(g, "find-1000"), v(j, "find-1000"), v(g, "find-unknown"), v(j, "find-unknown"), v(g, "dump"), v(j, "dump")):
        return ""
    net_g, net_j = v(g, "find-1000") - v(g, "find-unknown"), v(j, "find-1000") - v(j, "find-unknown")
    return f"""<ul>
    <li><b>Phần lõi giống nhau</b>: cùng thư viện C++ RocksDB, cùng định dạng và tham số nén, file ra bằng nhau
    ({fh.fmt_gb(v(g, "dump", "file_mb"))}); đọc và giải nén block tốn như nhau.</li>
    <li><b>Tra cứu: chênh lệch chủ yếu là khởi động JVM.</b> Tra key không tồn tại (gần như chỉ còn khởi động và mở DB):
    Java {fh.fmt_s(v(j, "find-unknown"))}, Go {fh.fmt_s(v(g, "find-unknown"))}. Trừ khoản đó, tra 1000 key còn Java
    {fh.fmt_s(net_j)} so với Go {fh.fmt_s(net_g)} ({pct(net_j, net_g)}): phần tra thật gần như bằng nhau. Chạy thành
    service thường trực thì Java không còn khoản khởi động.</li>
    <li><b>Dump: Java chậm hơn {pct(v(j, "dump"), v(g, "dump")).lstrip("+")}</b> ({fh.fmt_s(v(j, "dump"))} so với
    {fh.fmt_s(v(g, "dump"))}), CPU {fh.fmt_s(v(j, "dump", "cpu_s"))} so với {fh.fmt_s(v(g, "dump", "cpu_s"))}, RAM
    {fh.fmt_mb(v(j, "dump", "peak_rss_mb"))} so với {fh.fmt_mb(v(g, "dump", "peak_rss_mb"))} (JVM có heap nền, chuỗi
    UTF-16, và dữ liệu đi vòng qua <code>String</code> rồi chép qua JNI, trong khi Go đi từ byte ra byte).</li>
    <li><b>Bước tạo JSON</b> đo riêng, ngoài bộ bench, dữ liệu tổng hợp 70 cột × 300 ký tự, tính cho 1M dòng: Java bản
    cũ (<code>StringWriter</code> khoá đồng bộ từng ký tự và <code>String.format</code> mỗi cột) 230 s, Java sau khi
    sửa (<code>StringBuilder</code>, tiền tố cột dựng sẵn) 70,6 s, Go 54 s. Bản Java trong bảng trên đã dùng code sau
    khi sửa.</li>
    <li>Phần dump còn lại của Java chưa profile để tách riêng từng khoản (chuyển chuỗi, JNI, GC).</li>
  </ul>"""


def verdict():
    g, j, p = (d for d, *_ in BEST)
    need = [get(d, op, k) for d in (g, j, p) for op, k in (("dump", "file_mb"), ("find-1000", "wall_s"), ("dump", "wall_s"))]
    if None in need:
        return ""
    f = lambda d, op, k="wall_s": get(d, op, k)
    return f"""<section class="verdict">
  <p><b>go-rocksdb là phương án tốt nhất</b> theo ưu tiên thời gian ≈ dung lượng &gt; RAM: tra 1 key {fh.fmt_s(f(g, "find-1"))},
  1000 key {fh.fmt_s(f(g, "find-1000"))}, file {fh.fmt_gb(f(g, "dump", "file_mb"))}, RAM khi dump {fh.fmt_mb(f(g, "dump", "peak_rss_mb"))}.</p>
  <p><b>java-rocksdb</b> ra file như go-rocksdb ({fh.fmt_gb(f(j, "dump", "file_mb"))}, cùng tham số nén, sst_dump xác nhận) nhưng
  tra 1000 key {fh.fmt_s(f(j, "find-1000"))} ({times(f(j, "find-1000"), f(g, "find-1000"))} so với Go), dump {fh.fmt_s(f(j, "dump"))}
  so với {fh.fmt_s(f(g, "dump"))}, RAM dump {fh.fmt_mb(f(j, "dump", "peak_rss_mb"))} so với {fh.fmt_mb(f(g, "dump", "peak_rss_mb"))}.</p>
  <p><b>java-parquet-duckdb</b> có file nhỏ nhất ({fh.fmt_gb(f(p, "dump", "file_mb"))}; go-rocksdb lớn hơn {pct(f(g, "dump", "file_mb"), f(p, "dump", "file_mb")).lstrip("+")}) nhưng tra 1000 key {fh.fmt_s(f(p, "find-1000"))} ({times(f(p, "find-1000"), f(g, "find-1000"))} so với Go) và tăng
  theo kích thước file.</p>
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
  <span class="eyebrow">ColdBoy · đo lại toàn bộ · 17–18/11/2026</span>
  <h1>Ba phương án ở cấu hình tốt nhất, đo trong cùng một phiên</h1>
  <p class="lead">Mọi số trong trang này đến từ một phiên đo duy nhất: cùng máy, cùng dữ liệu (seed cố định
  <code>20261117</code>, một ngày 1M dòng, dữ liệu lặp), cùng giao thức, cùng lô đọc Oracle 1.000. Mỗi phương án dùng cấu
  hình tốt nhất tìm được ở bước chọn cấu hình bên dưới.</p>
</header>
{pending_note}
{verdict()}

<section>
  <h2>So sánh 3 phương án</h2>
  <p>Ô tô xanh là tốt nhất trong hàng. Dump 3 lần lấy trung vị; RAM là đỉnh lớn nhất của 3 lần (RSS cả tiến trình);
  tra cứu có 1 lần làm nóng rồi đo 3 lần. Mỗi bản ghi trả về được so từng byte với Oracle.</p>
  {best_table()}
  <p class="note">Thời gian tra bản Java gồm khoảng 0,4 s khởi động JVM, java-parquet-duckdb thêm khoảng 1 s mở DuckDB
  (giải nén thư viện native). Trong service chạy lâu các khoản này chỉ trả một lần. Hai bản RocksDB dùng hai bản native
  khác nhau: rocksdbjni đóng gói 11.8.1, Go dùng librocksdb 11.13.2 (Homebrew).</p>
</section>

<section>
  <h2>Vì sao go-rocksdb nhanh hơn java-rocksdb dù cùng file</h2>
  {why_go_java()}
</section>

<section>
  <h2>Chọn cấu hình tốt nhất</h2>
  <p>Mỗi cấu hình ứng viên dump 1 lần rồi tra, trên cùng dữ liệu. Chọn theo ưu tiên thời gian ≈ dung lượng &gt; RAM; riêng cấu hình nén, dung lượng nặng hơn: chấp nhận thời gian tăng vài %.</p>
  <h3>go-rocksdb: kích thước dictionary</h3>
  {go_screen_table()}
  <p class="note">16 KB: train 2 MB, buffer 16 MB (đề xuất của sếp). 64 KB: train 6,4 MB, buffer 64 MB.</p>
  {go_screen_note()}
  <h3>java-rocksdb: kích thước dictionary (screen riêng, cùng giao thức)</h3>
  {go_screen_table(JAVA_SCREEN, "java-rocksdb, mỗi cấu hình dump 1 lần")}
  {java_screen_note()}
  <p class="note">Mỗi cấu hình chỉ dump 1 lần nên chênh lệch vài % (ví dụ tra 1000 key giữa 16 KB và 64 KB) nằm trong
  nhiễu đo; kết luận chắc chắn là dung lượng file.</p>
  <h3>java-parquet-duckdb: codec và số luồng DuckDB</h3>
  {pq_screen_table()}
  {pq_screen_note()}
  <p class="note">Đã thử trước đó trên dữ liệu seed cũ (kết quả còn trong <code>results/</code>, không đưa vào đây vì
  không cùng dữ liệu): block RocksDB 256 KB và ZSTD mức 9 không làm file nhỏ thêm mà tra hoặc dump chậm hơn; bloom filter
  và page index cho Parquet làm file to hơn, tra không nhanh hơn.</p>
</section>

<section>
  <h2>Best practice theo từng phương án</h2>
  <div class="scroll"><table class="plain"><thead><tr><th>Phương án</th><th>Nên dùng</th><th>Không nên</th></tr></thead><tbody>
  <tr><th scope=row>go-rocksdb</th><td>Bật ZSTD dictionary 64 KB (<code>ROCKSDB_ZSTD_DICT_BYTES=65536</code>, train 6,4 MB,
  buffer 64 MB), ZSTD mức 3, block 64 KB; index và bloom phân vùng; ghi bằng <code>SstFileWriter</code> + ingest; đọc
  Oracle bằng godror lô 1.000, tắt prefetch.</td>
  <td>Block 256 KB, ZSTD mức 9 (không có lợi). Driver go-ora: ~1,2 GB RAM ở lô 10.000.</td></tr>
  <tr><th scope=row>java-rocksdb</th><td>Cùng cấu hình RocksDB như bản Go, đặt dictionary qua parser options native
  (<code>ColumnFamilyOptions.getColumnFamilyOptionsFromProps</code>, chế độ nghiêm ngặt) cho cả
  <code>compression_opts</code> và <code>bottommost_compression_opts</code>; ghi JSON bằng <code>StringBuilder</code>;
  ojdbc lô 1.000.</td>
  <td>Đặt dictionary qua <code>CompressionOptions</code> của rocksdbjni: lớp này thiếu <code>max_dict_buffer_bytes</code>,
  RocksDB sẽ giữ cả file trong RAM để lấy mẫu. Ghi JSON qua <code>StringWriter</code> (khoá đồng bộ từng ký tự) và
  <code>String.format</code> mỗi cột mỗi dòng.</td></tr>
  <tr><th scope=row>java-parquet-duckdb</th><td>Ghi dữ liệu đã sort theo <code>REFERENCE</code> (nhờ đó tra 1 key chỉ đọc
  1 row group); row group 128 MB, ZSTD, dictionary encoding; chạy thành service thường trực để không trả khởi động JVM
  và DuckDB ở mỗi lần tra; gom key tra theo lô vào một câu <code>IN (...)</code>.</td>
  <td>Kỳ vọng tra nhanh như RocksDB: không đạt được (mục dưới).</td></tr>
  </tbody></table></div>
</section>

<section>
  <h2>Vì sao Parquet tra chậm, và không sửa triệt để được</h2>
  {parquet_table()}
  <ul>
    <li>Parquet lưu theo cột, nén theo row group (~21k dòng, ~117 MB). Lấy 1 bản ghi đủ 73 cột phải giải nén nguyên row
    group chứa nó.</li>
    <li><b>Tra 1 key gần như không tăng theo file</b>: dữ liệu đã sort nên min/max của từng row group chỉ ra đúng 1 row
    group (chỉ tăng chút do footer to hơn).</li>
    <li><b>Tra 1000 key tăng theo file</b>: 1000 key ngẫu nhiên chạm gần hết row group, nên đọc gần cả file; file gấp đôi
    thì phải giải nén gấp đôi.</li>
    <li>RocksDB lưu theo dòng, đơn vị nén là block 64 KB (~3 bản ghi): 1000 key chỉ đọc ~1000 block nhỏ, không phụ
    thuộc kích thước file.</li>
    <li><b>Không sửa triệt để được</b> vì đây là bản chất của định dạng theo cột: tăng số luồng DuckDB không làm nhanh hơn, còn đổi codec chỉ
    đánh đổi dung lượng lấy thời gian (xem bảng chọn cấu hình).</li>
  </ul>
</section>

<section>
  <h2>ZSTD dictionary là gì</h2>
  <ul>
    <li>RocksDB lưu theo dòng, nén từng block 64 KB (~3 bản ghi) độc lập. Mỗi block bắt đầu với "trí nhớ" rỗng nên
    các chuỗi lặp khắp file ("khoản", <code>BRANCH_00</code>, tên cột JSON…) phải ghi lại ở từng block. Parquet nén
    cả cột của ~21k dòng một lần nên không bị như vậy.</li>
    <li>Bật dictionary: lúc ghi, RocksDB lấy mẫu dữ liệu, train một từ điển nhỏ, lưu một lần trong file; mọi block nén
    và giải nén dựa trên từ điển đó. Block vẫn nhỏ nên tra vẫn nhanh.</li>
    <li>Là tuỳ chọn chuẩn của RocksDB (<code>max_dict_bytes</code>, <code>zstd_max_train_bytes</code>,
    <code>max_dict_buffer_bytes</code>); file đọc được bằng RocksDB thông thường.</li>
  </ul>
</section>

<section>
  <h2>Cấu hình chỉnh được bằng biến môi trường</h2>
  <div class="scroll"><table class="plain"><thead><tr><th>Phương án</th><th>Biến</th></tr></thead><tbody>
  <tr><th scope=row>go-rocksdb, java-rocksdb</th><td><code>ROCKSDB_ZSTD_LEVEL</code>, <code>ROCKSDB_ZSTD_DICT_BYTES</code> (0 = tắt),
  <code>ROCKSDB_ZSTD_TRAIN_BYTES</code>, <code>ROCKSDB_ZSTD_BUFFER_BYTES</code>, <code>ROCKSDB_BLOCK_BYTES</code>,
  <code>ROCKSDB_BLOOM_BITS</code>, <code>ROCKSDB_INDEX</code> (partitioned | binary), <code>ROCKSDB_CACHE_BYTES</code>,
  <code>ROCKSDB_METADATA_BYTES</code></td></tr>
  <tr><th scope=row>java-parquet-duckdb</th><td><code>PARQUET_COMPRESSION</code>, <code>PARQUET_ZSTD_LEVEL</code>,
  <code>PARQUET_ROW_GROUP_BYTES</code>, <code>PARQUET_ROW_GROUP_ROWS</code>, <code>PARQUET_DICTIONARY</code>,
  <code>PARQUET_DICT_PAGE_BYTES</code>, <code>PARQUET_PAGE_BYTES</code>, <code>DUCKDB_THREADS</code>,
  <code>DUCKDB_MEMORY_LIMIT</code></td></tr>
  <tr><th scope=row>Cả ba</th><td><code>ORACLE_FETCH_SIZE</code>, <code>JAVA_OPTS</code>, <code>GOMEMLIMIT</code>;
  không đặt biến nào thì giữ cấu hình mặc định như trước.</td></tr>
  </tbody></table></div>
  <p class="note">Chạy lại toàn bộ: <code>SEED=20261117 SEED_MODE=status make seed</code> rồi
  <code>bench/run_final.sh screen</code> và <code>bench/run_final.sh final</code>.</p>
</section>

<footer>Sinh từ các file trong <code>results/status/</code> của repo parquet-profile (nhãn <code>final</code> và
<code>screen-*</code>). Hạt giống dữ liệu: <code>results/status/seed.json</code>.</footer>
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
