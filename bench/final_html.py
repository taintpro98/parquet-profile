"""Vẽ final.html ở thư mục gốc từ results/: 3 candidate, đo lại từ đầu với một câu SELECT cho cả ngày
(ORACLE_REPEAT_SINGLE=true, nhãn `single`), ở 1M / 2M / 3M dòng. Thiếu số thì ghi 'đang đo'.

Chạy qua `make summary` (bench.py summary) hoặc thẳng `python3 bench/final_html.py`.
final-report.html (bench/final_report.py) là bản trước, giữ nguyên.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "final.html"

CANDIDATES = [
    {"id": "java-rocksdb", "dir": "java-rocksdb-single", "name": "java-rocksdb", "role": "Đề xuất",
     "format": "RocksDB (SST), index + bloom phân vùng, ZSTD", "stack": "Java 25, rocksdbjni, ojdbc lô 10.000"},
    {"id": "go-rocksdb", "dir": "go-rocksdb-single", "name": "go-rocksdb", "role": "Đối trọng",
     "format": "RocksDB (SST), cùng cấu hình với bản Java", "stack": "Go, grocksdb, godror lô 1.000, tắt prefetch"},
    {"id": "java-parquet", "dir": "java-parquet-duckdb-single", "name": "java-parquet-duckdb", "role": "Để so",
     "format": "Parquet, row group 128 MB, dictionary, ZSTD", "stack": "Java 25, parquet-java ghi, DuckDB đọc, ojdbc lô 10.000"},
]
DATASETS = [("random", "Dữ liệu ngẫu nhiên"), ("status", "Dữ liệu lặp (20% cột là trạng thái)")]
SIZES = [("", "1M"), ("-2m", "2M"), ("-3m", "3M")]
MILLIONS = {"1M": 1, "2M": 2, "3M": 3}
OPS = ["dump", "find-1", "find-1000", "find-unknown"]


def load(c, ds, sfx, op):
    p = ROOT / "results" / ds / f"{c['dir']}{sfx}" / f"{op}.json"
    if not p.exists():
        return None
    s = json.loads(p.read_text())["summary"]
    return s if s.get("ok") else None


DATA = {(c["id"], ds, sz, op): load(c, ds, sfx, op)
        for c in CANDIDATES for ds, _ in DATASETS for sfx, sz in SIZES for op in OPS}
PENDING = '<span class="pending">đang đo</span>'
_skip = ROOT / "results" / "skipped.txt"  # cỡ bị bỏ vì thiếu ổ đĩa, do script chạy đêm ghi
SKIPPED = set(_skip.read_text().split()) if _skip.exists() else set()
MEASURED = [sz for _, sz in SIZES if sz not in SKIPPED]
MEASURED_TEXT = ", ".join(MEASURED[:-1]) + " và " + MEASURED[-1] if len(MEASURED) > 1 else MEASURED[0]


def get(cid, ds, sz, op, key):
    s = DATA[(cid, ds, sz, op)]
    return None if s is None else s.get(key)


def fmt_s(v):
    if v is None:
        return PENDING
    if v >= 60:
        return f"{v:,.0f} s".replace(",", ".")
    return f"{v:.2f} s".replace(".", ",") if v < 10 else f"{v:.1f} s".replace(".", ",")


def fmt_mb(v):
    if v is None:
        return PENDING
    return f"{v/1024:.2f} GB".replace(".", ",") if v >= 1024 else f"{v:.0f} MB"


def fmt_gb(v):
    return PENDING if v is None else f"{v/1000:.1f} GB".replace(".", ",")


def best(values, cell):
    """Đánh dấu ô nhỏ nhất trong hàng (chỉ khi đủ số)."""
    nums = [v for v in values if v is not None]
    low = min(nums) if len(nums) == len(values) and nums else None
    return [f'<td class="num{" best" if v is not None and v == low else ""}">{cell(v)}</td>' for v in values]


def head():
    cols = "".join(f'<th class="num">{c["name"]}<small>{c["role"]}</small></th>' for c in CANDIDATES)
    return f"<thead><tr><th>Chỉ số</th>{cols}</tr></thead>"


def row(label, values, cell, note=""):
    n = f"<small>{note}</small>" if note else ""
    return f"<tr><th scope=row>{label}{n}</th>{''.join(best(values, cell))}</tr>"


def table_dataset(ds):
    rows = []
    for _, sz in SIZES:
        if not any(DATA[(c["id"], ds, sz, op)] for c in CANDIDATES for op in OPS) and sz in SKIPPED:
            rows.append(f'<tr class="group"><th colspan="{len(CANDIDATES)+1}">Một ngày {sz} dòng · không đo: không đủ ổ đĩa</th></tr>')
            continue
        rows.append(f'<tr class="group"><th colspan="{len(CANDIDATES)+1}">Một ngày {sz} dòng</th></tr>')
        v = lambda op, k: [get(c["id"], ds, sz, op, k) for c in CANDIDATES]
        rows.append(row("Dump · thời gian", v("dump", "wall_s"), fmt_s))
        rows.append(row("Dump · RAM đỉnh", v("dump", "peak_rss_mb"), fmt_mb))
        rows.append(row("Dung lượng file", v("dump", "file_mb"), fmt_gb))
        rows.append(row("Tra 1 key · thời gian", v("find-1", "wall_s"), fmt_s, "gồm khởi động tiến trình"))
        rows.append(row("Tra 1000 key · thời gian", v("find-1000", "wall_s"), fmt_s))
        rows.append(row("Tra 1000 key · RAM đỉnh", v("find-1000", "peak_rss_mb"), fmt_mb))
        rows.append(row("Tra key không tồn tại", v("find-unknown", "wall_s"), fmt_s))
        checks = []
        for c in CANDIDATES:
            s = DATA[(c["id"], ds, sz, "find-1000")]
            checks.append(PENDING if s is None else f'{s["matched"]}/{s["checked"]}')
        rows.append(f'<tr><th scope=row>Khớp Oracle từng byte</th>{"".join(f"<td class=num>{x}</td>" for x in checks)}</tr>')
    return f'<div class="scroll"><table>{head()}<tbody>{"".join(rows)}</tbody></table></div>'


def extrapolate(cid, ds, op, key):
    """Đường thẳng bình phương tối thiểu qua các cỡ đã đo (cần ít nhất 1M và 2M), lấy giá trị ở 10M."""
    pts = [(MILLIONS[sz], get(cid, ds, sz, op, key)) for _, sz in SIZES]
    pts = [(x, y) for x, y in pts if y is not None]
    if len(pts) < 2:
        return None
    mx, my = sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)
    slope = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, _ in pts)
    return my + slope * (10 - mx)


def table_10m():
    rows = []
    for ds, label in DATASETS:
        rows.append(f'<tr class="group"><th colspan="{len(CANDIDATES)+1}">{label}</th></tr>')
        v = lambda op, k: [extrapolate(c["id"], ds, op, k) for c in CANDIDATES]
        rows.append(row("Dump · thời gian", v("dump", "wall_s"), lambda x: PENDING if x is None else f"{x/3600:.1f} giờ".replace(".", ",")))
        rows.append(row("Dump · RAM đỉnh", v("dump", "peak_rss_mb"), fmt_mb))
        rows.append(row("Dung lượng / ngày", v("dump", "file_mb"), fmt_gb))
        rows.append(row("Dung lượng / năm", [None if x is None else x * 365 for x in v("dump", "file_mb")],
                        lambda x: PENDING if x is None else f"{x/1e6:.0f} TB"))
        rows.append(row("Tra 1000 key · thời gian", v("find-1000", "wall_s"), fmt_s))
    return f'<div class="scroll"><table>{head()}<tbody>{"".join(rows)}</tbody></table></div>'


def candidates_table():
    rows = "".join(
        f'<tr><th scope=row>{c["name"]}</th><td><span class="role role-{c["id"]}">{c["role"]}</span></td>'
        f'<td>{c["format"]}</td><td>{c["stack"]}</td></tr>' for c in CANDIDATES)
    return ('<div class="scroll"><table class="plain"><thead><tr><th>Phương án</th><th>Vai trò</th>'
            f'<th>Định dạng</th><th>Ngôn ngữ, thư viện</th></tr></thead><tbody>{rows}</tbody></table></div>')


def pick(cid, sz, op, key, ds="random"):
    return get(cid, ds, sz, op, key)


def num(v, f):
    return PENDING if v is None else f(v)


pending_cells = sum(1 for (cid, ds, sz, op), v in DATA.items() if v is None and sz not in SKIPPED)
pending_note = "" if not pending_cells else (
    f'<p class="callout">Còn {pending_cells} ô <span class="pending">đang đo</span>; chạy <code>make summary</code> '
    'sau khi bench xong để điền.</p>')
jr = lambda op, k: pick("java-rocksdb", "1M", op, k)
gr = lambda op, k: pick("go-rocksdb", "1M", op, k)
jp = lambda op, k: pick("java-parquet", "1M", op, k)
size_gain = None if jr("dump", "file_mb") is None or jp("dump", "file_mb") is None else (
    1 - jp("dump", "file_mb") / jr("dump", "file_mb")) * 100

HTML = f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Benchmark archive ColdBoy</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root {{
  --bg: #f6f7f9; --surface: #ffffff; --fg: #17202b; --muted: #5b6676; --line: #dfe3e9;
  --accent: #1f5fae; --accent-soft: #e6eefa; --best: #e3f4ea; --best-fg: #17603a;
  --warn: #fdf3dd; --warn-fg: #7a5200; --group: #eef1f5;
  color-scheme: light;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #0f141b; --surface: #161d27; --fg: #e6ebf1; --muted: #98a3b3; --line: #2a3442;
  --accent: #79a9ea; --accent-soft: #1c2a3d; --best: #173325; --best-fg: #8fd8ac;
  --warn: #332812; --warn-fg: #f0c674; --group: #1b2330; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #0f141b; --surface: #161d27; --fg: #e6ebf1; --muted: #98a3b3; --line: #2a3442;
  --accent: #79a9ea; --accent-soft: #1c2a3d; --best: #173325; --best-fg: #8fd8ac;
  --warn: #332812; --warn-fg: #f0c674; --group: #1b2330; color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.6 "Be Vietnam Pro", system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1040px; margin: 0 auto; padding-block: 40px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 36px; }}
header {{ display: grid; gap: 8px; }}
.eyebrow {{ font: 500 12px/1 "JetBrains Mono", ui-monospace, monospace; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }}
h1 {{ font-size: clamp(26px, 4vw, 34px); line-height: 1.2; margin: 0; text-wrap: balance; }}
h2 {{ font-size: 21px; margin: 0 0 12px; text-wrap: balance; }}
h3 {{ font-size: 16px; margin: 20px 0 8px; }}
p, li {{ max-width: 72ch; }}
p {{ margin: 0 0 10px; }}
ul {{ margin: 0 0 10px; padding-left: 20px; }}
.lead {{ color: var(--muted); font-size: 16px; margin: 0; }}
section {{ min-width: 0; }}
.verdict {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--accent);
  border-radius: 10px; padding: 20px 22px; display: grid; gap: 6px; }}
.verdict p {{ margin: 0; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; min-width: 0; }}
.card h3 {{ margin: 6px 0 4px; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 15px; }}
.card p {{ margin: 0; color: var(--muted); font-size: 14px; }}
.scroll {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; }}
table {{ border-collapse: collapse; width: 100%; min-width: 620px; font-size: 14px; }}
th, td {{ padding: 9px 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
thead th {{ font-weight: 600; color: var(--muted); font-size: 13px; background: var(--surface); }}
thead th small, tbody th small {{ display: block; font-weight: 400; color: var(--muted); font-size: 12px; }}
tbody th {{ font-weight: 500; }}
tr:last-child > * {{ border-bottom: 0; }}
.num {{ text-align: right; font-family: "JetBrains Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.best {{ background: var(--best); color: var(--best-fg); font-weight: 500; }}
tr.group th {{ background: var(--group); font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); font-weight: 600; }}
.pending {{ color: var(--muted); font-style: italic; font-family: "Be Vietnam Pro", system-ui, sans-serif; }}
.callout {{ background: var(--warn); color: var(--warn-fg); border-radius: 8px; padding: 10px 14px; margin: 0; }}
.role {{ display: inline-block; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 99px;
  background: var(--accent-soft); color: var(--accent); white-space: nowrap; }}
.role-java-parquet {{ background: var(--group); color: var(--muted); }}
.note {{ color: var(--muted); font-size: 13px; margin-top: 8px; }}
code {{ font-family: "JetBrains Mono", ui-monospace, monospace; font-size: .92em; }}
footer {{ color: var(--muted); font-size: 13px; border-top: 1px solid var(--line); padding-top: 16px; }}
</style>
</head>
<body>
<main>
<header>
  <span class="eyebrow">ColdBoy · lưu trữ dữ liệu giao dịch lạnh</span>
  <h1>Chọn định dạng archive cho ColdBoy</h1>
  <p class="lead">Đo lại từ đầu 3 phương án còn lại sau vòng loại, mỗi ngày đọc bằng một câu SELECT như khi chạy thật,
  ở một ngày {MEASURED_TEXT} dòng, rồi suy ra 10M dòng.</p>
</header>

<section class="verdict">
  <p><b>Đề xuất: java-rocksdb.</b> Ở 1M dòng ngẫu nhiên: dump {num(jr("dump","wall_s"),fmt_s)} / RAM
  {num(jr("dump","peak_rss_mb"),fmt_mb)}, tra 1000 key {num(jr("find-1000","wall_s"),fmt_s)}. Thời gian tra gần như
  không tăng khi file to ra.</p>
  <p><b>go-rocksdb</b> là đối trọng: cùng định dạng, file giống hệt; dump {num(gr("dump","wall_s"),fmt_s)} / RAM
  {num(gr("dump","peak_rss_mb"),fmt_mb)}, tra 1000 key {num(gr("find-1000","wall_s"),fmt_s)}. Cần Oracle Instant
  Client và cgo.</p>
  <p><b>java-parquet-duckdb</b> nén tốt hơn {num(size_gain, lambda x: f"{x:.0f}%")}, nhưng dump cần RAM
  {num(jp("dump","peak_rss_mb"),fmt_mb)} và tra 1000 key mất {num(jp("find-1000","wall_s"),fmt_s)}, tăng theo
  kích thước file.</p>
</section>
{pending_note}

<section>
  <h2>Bài toán và tiêu chí</h2>
  <ul>
    <li>Truy vấn duy nhất: <code>REFERENCE</code> + <code>CREATED_AT</code> → một bản ghi đầy đủ 73 cột. <code>CREATED_AT</code>
    chọn archive của ngày, <code>REFERENCE</code> tìm bản ghi trong đó.</li>
    <li>Mỗi ngày khoảng 10 triệu dòng, ghi một lần rồi chỉ đọc.</li>
    <li>Pod tối đa 4 CPU / 4 GiB cho cả dump lẫn tra cứu.</li>
    <li>Thứ tự ưu tiên: <b>RAM thấp</b> &gt; <b>dung lượng thấp</b> &gt; <b>thời gian thấp</b>.</li>
    <li>Chỉ dùng định dạng và thư viện chuẩn, không tự chế định dạng hay index riêng.</li>
  </ul>
</section>

<section>
  <h2>Ba phương án</h2>
  {candidates_table()}
</section>

<section>
  <h2>Kết quả đo</h2>
  <p>Ô tô xanh là tốt nhất trong hàng. Thời gian là trung vị, RAM là đỉnh (RSS của cả tiến trình), tra cứu có 1 lần làm
  nóng rồi đo 3 lần. Mỗi bản ghi trả về được so từng byte với bản ghi đọc thẳng từ Oracle.</p>
  {"".join(f"<h3>{label}</h3>{table_dataset(ds)}" for ds, label in DATASETS)}
  <p class="note">Tra cứu bản Java gồm khoảng 0,4 s khởi động JVM ở mỗi lần đo; trong service chạy lâu, phần này không có.</p>
</section>

<section>
  <h2>Suy ra một ngày 10M dòng</h2>
  <p>Ngoại suy bằng đường thẳng qua các cỡ đã đo (bình phương tối thiểu). Với thời gian tra của RocksDB, cách này cho
  số cao hơn thực tế vì index chỉ tăng theo logarit.</p>
  {table_10m()}
  <p class="note">Lần đo trước, bench chạy lại câu SELECT ở mỗi lượt 100k dòng; với godror cách đó làm RAM của Go
  tăng giả (1M dòng: 328 MB so với 179 MB khi đọc bằng một câu SELECT). Lần này cả 3 phương án đều đọc cả ngày bằng
  một câu SELECT.</p>
</section>

<section>
  <h2>Vì sao chọn RocksDB</h2>
  <ul>
    <li><b>RAM khi dump</b> của RocksDB thấp hơn Parquet (bảng trên), vì ghi tuần tự từng block đã sắp xếp; Parquet giữ
    cả row group 128 MB của 73 cột trong RAM trước khi ghi.</li>
    <li><b>Tra cứu</b> là truy vấn duy nhất của hệ thống. RocksDB có index và bloom filter theo key nên chỉ đọc đúng
    block chứa key; Parquet là định dạng theo cột, 1000 key ngẫu nhiên rơi vào gần như mọi row group nên phải giải nén
    gần hết file, và thời gian tra tăng gấp đôi khi file gấp đôi.</li>
    <li><b>Toàn vẹn</b>: mỗi block của RocksDB có checksum, được kiểm ở mỗi lần đọc; hỏng thì báo lỗi.</li>
    <li><b>Dung lượng</b> là điểm Parquet hơn, khoảng 11% (≈ 2,5–3 TB mỗi năm ở 10M dòng/ngày), không bù được RAM và
    tốc độ tra.</li>
  </ul>
  <h3>Java hay Go</h3>
  <ul>
    <li>Hai bản ghi ra file RocksDB giống nhau, đọc lẫn được của nhau.</li>
    <li>Java dùng ojdbc, driver chính thức của Oracle, không cần cài thêm gì.</li>
    <li>Go dùng godror, bọc thư viện C chính thức của Oracle (ODPI-C); cần Oracle Instant Client trong image và cgo.
    godror cấp buffer theo độ rộng khai báo của cột, nên lô fetch phải tính theo schema thật
    (RAM ≈ số dòng mỗi lô × tổng độ rộng khai báo của các cột).</li>
  </ul>
</section>

<section>
  <h2>Các phương án đã loại</h2>
  <ul>
    <li><b>Parquet có bloom filter và page index</b> (parquet-lookup): RAM khi dump tăng theo số dòng, file to hơn
    parquet-duckdb, tra 1000 key vẫn chậm như nhau.</li>
    <li><b>ORC</b>: RAM khi dump khoảng 1,7 GB, thời gian tra tăng theo kích thước file.</li>
    <li><b>Avro</b>: không có index theo key, tra 1000 key mất khoảng 7 phút, không có checksum trong file.</li>
    <li><b>Bản Go của Parquet</b>: RAM khi dump 2,6–3 GB.</li>
    <li><b>Arrow + ZSTD, row block tự chế</b>: định dạng tự chế, không đạt yêu cầu dùng định dạng chuẩn.</li>
  </ul>
</section>

<section>
  <h2>Điều kiện đo và giới hạn</h2>
  <ul>
    <li>Máy Apple M1 Pro, 10 CPU, 32 GB; Oracle Database Free chạy trong Docker trên cùng máy. Java <code>-Xmx2g</code>,
    Go <code>GOMEMLIMIT=2GiB</code>.</li>
    <li>Dữ liệu là dữ liệu sinh ngẫu nhiên, không phải dữ liệu thật: 70 cột <code>VARCHAR2(1000)</code>, mỗi dòng
    khoảng 21 KB. Bộ "lặp" có 20% số cột là giá trị trạng thái lặp lại. Dữ liệu thật thường ngắn và lặp nhiều hơn; dung
    lượng và thời gian thực tế cần đo lại trên mẫu thật (<code>avg_row_len</code>).</li>
    <li>Một ngày 1M / 2M / 3M dòng tạo từ bảng 100k dòng: một câu SELECT gộp 10 / 20 / 30 lượt bằng
    <code>UNION ALL</code>, mỗi lượt thêm tiền tố vào <code>REFERENCE</code>. Lô fetch: ojdbc 10.000, godror 1.000 và tắt
    prefetch.</li>
    <li>Oracle ở xa qua mạng sẽ làm dump chậm hơn số đo ở đây; sắp xếp theo <code>REFERENCE</code> của cả ngày cần
    TEMP lớn ở Oracle, chưa đo được trong bench.</li>
  </ul>
</section>

<footer>Sinh từ các file trong <code>results/</code> của repo parquet-profile.</footer>
</main>
</body>
</html>
"""


def write():
    """Ghi final.html; trả đường dẫn và các ô còn thiếu số."""
    OUT.write_text(HTML, encoding="utf-8")
    missing = [f"{cid} {ds} {sz} {op}" for (cid, ds, sz, op), v in DATA.items()
               if v is None and sz not in SKIPPED]
    return OUT, missing


if __name__ == "__main__":
    path, missing = write()
    print(f"Đã vẽ {path.relative_to(ROOT)}" + (f" · còn {len(missing)} ô đang đo" if missing else ""))
