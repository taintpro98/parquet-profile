"""Runner chung cho mọi phương án. Đo từ bên ngoài tiến trình nên Java và Go được đo như nhau.

  python3 bench/bench.py list
  python3 bench/bench.py dump   --impl IMPL [--runs N] [--label L]
  python3 bench/bench.py find   --impl IMPL [--scenario S] [--cache warm|cold] [--runs N] [--label L]
  python3 bench/bench.py report

IMPL là tên phương án (java-parquet-duckdb), danh sách cách nhau bởi dấu phẩy, hoặc all.
Kết quả: results/results.csv (mỗi lần chạy 1 dòng), results/REPORT.md (bảng so sánh),
results/runs/<impl>/<thư mục từng lần chạy>/ (metrics.json, output.log, result.jsonl).
Archive của mỗi phương án nằm ở data/<impl>[-<label>]/, mỗi ngày một file YYYY-MM-DD.<đuôi>.
"""

import argparse
import csv
import json
import os
import platform
import shlex
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFS = ROOT / "bench" / "refs"
RESULTS = ROOT / "results"
CSV_FILE = RESULTS / "results.csv"
REPORT = RESULTS / "REPORT.md"
DATA = ROOT / "data"

FIELDS = ["batch", "time", "impl", "label", "op", "scenario", "cache", "run",
          "wall_s", "cpu_s", "peak_rss_mb", "file_mb", "rows", "found", "expected", "ok", "settings"]
# Biến môi trường ảnh hưởng tới số đo, được ghi kèm mỗi lần chạy.
SETTING_PREFIXES = ("PARQUET_", "DUCKDB_", "ARROW_", "BLOCK_", "ORACLE_REPEAT", "ORACLE_FETCH_SIZE", "ORACLE_QUERY",
                    "ORACLE_TABLE", "JAVA_OPTS", "GOGC", "GOMEMLIMIT", "GOMAXPROCS")


def java_command():
    java_home = os.environ.get("JAVA_HOME")
    java = str(Path(java_home) / "bin" / "java") if java_home else "java"
    opts = shlex.split(os.environ.get("JAVA_OPTS", ""))
    return [java, "--enable-native-access=ALL-UNNAMED", "--add-opens=java.base/java.nio=ALL-UNNAMED", *opts,
            "-jar", str(ROOT / "impl/java/target/archive.jar")]


LANGUAGES = {
    "java": java_command,
    "go": lambda: [str(ROOT / "impl/go/bin/archive")],
}


def known_impls():
    """{impl: (language, option, extension)} lấy từ lệnh list của từng ngôn ngữ."""
    impls = {}
    for lang, command in LANGUAGES.items():
        out = subprocess.run([*command(), "list"], capture_output=True, text=True, check=True).stdout
        for line in out.splitlines():
            option, ext = line.split("\t")
            impls[f"{lang}-{option}"] = (lang, option, ext)
    return impls


def select(impls, wanted):
    if wanted == "all":
        return list(impls)
    names = [w.strip() for w in wanted.split(",") if w.strip()]
    unknown = [n for n in names if n not in impls]
    if unknown:
        sys.exit(f"Không có phương án {unknown}. Có: {', '.join(impls)}")
    return names


def settings(op):
    """FIND không đọc Oracle, nên các biến ORACLE_* không được ghi cho FIND."""
    pairs = sorted((k, v) for k, v in os.environ.items()
                   if k.startswith(SETTING_PREFIXES) and v and not (op == "find" and k.startswith("ORACLE_")))
    return " ".join(f"{k}={v}" for k, v in pairs)


def drop_caches():
    if platform.system() == "Darwin":
        command = ["sudo", "-n", "purge"]
    else:
        command = ["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"]
    if subprocess.run(command).returncode != 0:
        sys.exit("CACHE=cold cần sudo không hỏi mật khẩu để xoá cache hệ điều hành: " + " ".join(command))


def measure(command, log_path):
    """Chạy lệnh, trả về (exit code, wall s, cpu s, peak RSS MB, dòng stdout cuối)."""
    with open(log_path, "w") as log:
        start = time.monotonic()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True, cwd=ROOT)
        stdout = process.stdout.read()
        _, status, usage = os.wait4(process.pid, 0)
        wall = time.monotonic() - start
        process.returncode = os.waitstatus_to_exitcode(status)
        log.write(stdout)
    # macOS báo ru_maxrss theo byte, Linux theo KB.
    rss_bytes = usage.ru_maxrss if platform.system() == "Darwin" else usage.ru_maxrss * 1024
    lines = stdout.strip().splitlines()
    return process.returncode, wall, usage.ru_utime + usage.ru_stime, rss_bytes / 2**20, lines[-1] if lines else ""


def run(args):
    impls = known_impls()
    names = select(impls, args.impl)
    batch = datetime.now().strftime("%Y%m%d-%H%M%S")
    scenarios = sorted(p.stem for p in REFS.glob("*.txt")) if args.scenario == "all" else args.scenario.split(",")
    rows = []
    for name in names:
        lang, option, ext = impls[name]
        archive = DATA / (name + (f"-{args.label}" if args.label else ""))  # thư mục, mỗi ngày một file
        jobs = [None] if args.op == "dump" else scenarios
        for scenario in jobs:
            refs = REFS / f"{scenario}.txt" if scenario else None
            if refs and not refs.exists():
                sys.exit(f"Không có {refs.relative_to(ROOT)}")
            if args.op == "find" and not any(archive.glob(f"*.{ext}")):
                sys.exit(f"Chưa có {archive.relative_to(ROOT)}, chạy make dump IMPL={name} trước")
            if args.op == "find" and args.cache == "warm":
                run_once(name, lang, option, archive, refs, batch, args, scenario, run=0)  # làm nóng, không ghi
            for i in range(1, args.runs + 1):
                if args.op == "find" and args.cache == "cold":
                    drop_caches()
                rows.append(run_once(name, lang, option, archive, refs, batch, args, scenario, run=i))
    write_csv(rows)
    write_report()
    print(f"\nĐã ghi {len(rows)} lần chạy → {CSV_FILE.relative_to(ROOT)}, {REPORT.relative_to(ROOT)}")
    if not all(r["ok"] == "true" for r in rows):
        sys.exit("Có lần chạy KHÔNG đúng (ok=false), xem output.log trong results/runs/")


def run_once(name, lang, option, archive, refs, batch, args, scenario, run):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = f"-{args.label}" if args.label else ""
    run_dir = RESULTS / "runs" / name / f"{args.op}{'-' + scenario if scenario else ''}{label}-{stamp}-{run}"
    run_dir.mkdir(parents=True, exist_ok=True)
    command = [*LANGUAGES[lang](), args.op, "--impl", option, "--archive", str(archive)]
    expected = ""
    if args.op == "find":
        command += ["--refs", str(refs), "--out", str(run_dir / "result.jsonl")]
        unique = {line.strip() for line in refs.read_text().splitlines() if line.strip()}
        expected = 0 if scenario.startswith("missing") else len(unique)

    tag = f"{name}{label} {args.op}{' ' + scenario if scenario else ''}"
    print(f"→ {tag} {'(làm nóng)' if run == 0 else f'lần {run}/{args.runs}'} ...", flush=True)
    code, wall, cpu, rss, last = measure(command, run_dir / "output.log")
    result = json.loads(last) if code == 0 and last.startswith("{") else {}
    ok = code == 0 and (result.get("rows", 0) > 0 if args.op == "dump" else result.get("found") == expected)
    row = {
        "batch": batch, "time": stamp, "impl": name, "label": args.label, "op": args.op,
        "scenario": scenario or "", "cache": args.cache if args.op == "find" else "", "run": run,
        "wall_s": f"{wall:.3f}", "cpu_s": f"{cpu:.3f}", "peak_rss_mb": f"{rss:.0f}",
        "file_mb": f"{archive_size(archive) / 2**20:.0f}" if args.op == "dump" else "",
        "rows": result.get("rows", ""), "found": result.get("found", ""), "expected": expected,
        "ok": str(ok).lower(), "settings": settings(args.op),
    }
    (run_dir / "metrics.json").write_text(json.dumps(row, indent=2, ensure_ascii=False) + "\n")
    print(f"  wall {row['wall_s']} s · cpu {row['cpu_s']} s · RAM {row['peak_rss_mb']} MB"
          f"{' · file ' + row['file_mb'] + ' MB' if row['file_mb'] else ''} · {'OK' if ok else 'SAI'}")
    return row if run > 0 else None


def archive_size(directory):
    """Tổng dung lượng các file ngày trong thư mục archive."""
    return sum(f.stat().st_size for f in directory.glob("*") if f.is_file() and not f.name.endswith(".tmp"))


def write_csv(rows):
    rows = [r for r in rows if r]
    RESULTS.mkdir(exist_ok=True)
    new = not CSV_FILE.exists()
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        writer.writerows(rows)


def write_report():
    """Mỗi nhóm (thao tác, kịch bản, cache, phương án, nhãn) lấy lần bench gần nhất.
    Thời gian lấy trung vị, RAM lấy lớn nhất; giá trị tốt nhất mỗi cột in đậm."""
    if not CSV_FILE.exists():
        sys.exit("Chưa có kết quả nào trong results/results.csv")
    with open(CSV_FILE, newline="") as f:
        all_rows = list(csv.DictReader(f))
    latest = {}
    for r in all_rows:
        key = (r["op"], r["scenario"], r["cache"], r["impl"], r["label"])
        latest.setdefault(key, {}).setdefault(r["batch"], []).append(r)

    sections = {}
    for (op, scenario, cache, impl, label), batches in latest.items():
        runs = batches[max(batches)]
        median = lambda field: statistics.median(float(r[field]) for r in runs)
        oks = sum(r["ok"] == "true" for r in runs)
        sections.setdefault((op, scenario, cache), []).append({
            "impl": impl + (f" `{label}`" if label else ""),
            "runs": len(runs),
            "wall": median("wall_s"),
            "cpu": median("cpu_s"),
            "ram": max(float(r["peak_rss_mb"]) for r in runs),
            "file": float(runs[-1]["file_mb"]) if runs[-1]["file_mb"] else None,
            "rows": runs[-1]["rows"] or runs[-1]["found"],
            "ok": "✓" if oks == len(runs) else f"✗ {oks}/{len(runs)}",
            "settings": runs[-1]["settings"],
            "date": datetime.strptime(runs[-1]["time"], "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M"),
        })

    lines = ["# Benchmark", "",
             f"Cập nhật {datetime.now():%Y-%m-%d %H:%M} · {platform.system()} {platform.machine()} · "
             f"{os.cpu_count()} CPU", "",
             "Thời gian: trung vị các lần chạy · RAM: Peak RSS lớn nhất của cả tiến trình · "
             "**đậm** = tốt nhất cột · đo từ ngoài tiến trình, gồm cả khởi động JVM/DuckDB.", ""]
    known = ["one", "many-1000", "missing"]
    order = lambda key: (key[0] != "dump", known.index(key[1]) if key[1] in known else len(known), key[1], key[2])
    for op, scenario, cache in sorted(sections, key=order):
        entries = sorted(sections[(op, scenario, cache)], key=lambda e: e["impl"])
        title = "DUMP" if op == "dump" else f"FIND `{scenario}` · cache {cache}"
        columns = [("Phương án", "impl", None), ("Lần", "runs", None), ("Wall (s)", "wall", "{:.2f}"),
                   ("CPU (s)", "cpu", "{:.2f}"), ("Peak RAM (MB)", "ram", "{:.0f}")]
        if op == "dump":
            columns.append(("File (MB)", "file", "{:.0f}"))
        columns += [("Dòng" if op == "dump" else "Tìm thấy", "rows", None), ("Đúng", "ok", None),
                    ("Ngày", "date", None)]
        best = {key: min(e[key] for e in entries if e[key] is not None)
                for _, key, fmt in columns if fmt and any(e[key] is not None for e in entries)}
        lines += [f"## {title}", "",
                  "| " + " | ".join(c[0] for c in columns) + " |",
                  "|" + "|".join("---:" if c[2] or c[1] == "runs" else "---" for c in columns) + "|"]
        for e in entries:
            cells = []
            for _, key, fmt in columns:
                value = e[key]
                if fmt and value is not None:
                    text = fmt.format(value)
                    cells.append(f"**{text}**" if len(entries) > 1 and value == best[key] else text)
                else:
                    cells.append("" if value is None else str(value))
            lines.append("| " + " | ".join(cells) + " |")
        configs = [f"- {e['impl']}: `{e['settings']}`" for e in entries if e["settings"]]
        if configs:
            lines += ["", "Cấu hình:", *configs]
        lines.append("")
    REPORT.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="op", required=True)
    sub.add_parser("list")
    sub.add_parser("report")
    for op in ("dump", "find"):
        p = sub.add_parser(op)
        p.add_argument("--impl", required=True)
        p.add_argument("--runs", type=int, default=1 if op == "dump" else 5)
        p.add_argument("--label", default="", help="tên cấu hình, ví dụ rg50k; tách file archive và dòng báo cáo")
        p.add_argument("--scenario", default="all",
                       help="tên file trong bench/refs (mỗi dòng REFERENCE,TIMESTAMP), cách nhau bởi dấu phẩy, hoặc all")
        p.add_argument("--cache", choices=["warm", "cold"], default="warm")
    args = parser.parse_args()
    if args.op == "list":
        for name, (_, _, ext) in known_impls().items():
            print(f"{name}\t.{ext}")
    elif args.op == "report":
        write_report()
        print(REPORT.relative_to(ROOT))
    else:
        run(args)


if __name__ == "__main__":
    main()
