"""Runner chung cho mọi phương án. Đo từ bên ngoài tiến trình nên Java và Go được đo như nhau.

  python3 bench/bench.py list
  python3 bench/bench.py sample       [--size 1000]
  python3 bench/bench.py dump         --impl IMPL [--runs N] [--label L]
  python3 bench/bench.py find         --impl IMPL --n N [--cache warm|cold] [--runs N] [--label L]
  python3 bench/bench.py find-unknown --impl IMPL [--cache warm|cold] [--runs N] [--label L]
  python3 bench/bench.py summary

IMPL là tên phương án (java-parquet-duckdb), danh sách cách nhau bởi dấu phẩy, hoặc all.
SEED_MODE (random | status) chọn bộ dữ liệu, ORACLE_REPEAT chọn số lượt (10 = 1M dòng/ngày).

sample bốc key ngẫu nhiên từ Oracle kèm bản ghi đầy đủ, lưu ở bench/samples/<SEED_MODE>/r<ORACLE_REPEAT>/
và dùng chung cho mọi phương án. find --n N tra N key đầu của bộ đó rồi so từng cột với Oracle.

Mỗi lệnh ghi một file kết quả, ghi đè đúng file của phần vừa chạy:
  results/<SEED_MODE>/<impl>[-<label>]/dump.json | find-<N>.json | find-unknown.json  (+ -cold)
  results/<SEED_MODE>/<impl>[-<label>]/logs/      (output của lần chạy gần nhất)
  results/<SEED_MODE>/oracle.json                 (make seed ghi)
summary đọc các file đó và vẽ final.html ở thư mục gốc (3 candidate); phần chưa có kết quả ghi "đang đo".
Archive nằm ở data/<impl>-<SEED_MODE>[-<label>]/, mỗi ngày một file YYYY-MM-DD.<đuôi>.
"""

import argparse
import json
import os
import platform
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODE = os.environ.get("SEED_MODE", "random")  # bộ dữ liệu: random | status
REPEAT = int(os.environ.get("ORACLE_REPEAT", "1"))
SAMPLES = ROOT / "bench" / "samples" / MODE / f"r{REPEAT}"
RESULTS = ROOT / "results"
DATA = ROOT / "data"

# Biến môi trường ảnh hưởng tới số đo, được ghi kèm mỗi kết quả.
SETTING_PREFIXES = ("PARQUET_", "DUCKDB_", "ARROW_", "BLOCK_", "ROCKSDB_", "ORC_", "AVRO_", "ORACLE_REPEAT", "ORACLE_FETCH_SIZE", "ORACLE_PREFETCH_ROWS", "ORACLE_DRIVER", "ORACLE_QUERY",
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


def machine():
    return f"{platform.system()} {platform.machine()} · {os.cpu_count()} CPU"


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


def archive_size(directory):
    """Tổng dung lượng các file ngày trong thư mục archive (có phương án lưu một ngày là một thư mục)."""
    return sum(f.stat().st_size for f in directory.rglob("*") if f.is_file() and ".tmp" not in f.parts[-1])


def sample(size):
    """Bốc key ngẫu nhiên từ Oracle (lệnh sample của bản Go), dùng chung cho mọi phương án."""
    command = [*LANGUAGES["go"](), "sample", "--size", str(size), "--out-dir", str(SAMPLES)]
    if subprocess.run(command, cwd=ROOT).returncode != 0:
        sys.exit("make sample thất bại")
    meta = json.loads((SAMPLES / "meta.json").read_text())
    print(f"Bộ key {meta['id']}: {size} key, {MODE}, {REPEAT} lượt → {SAMPLES.relative_to(ROOT)}")


def load_sample(n):
    """N key đầu của bộ đã bốc, cùng đáp án theo reference. Chưa có bộ nào thì tự bốc."""
    if not (SAMPLES / "meta.json").exists():
        sample(max(1000, n or 0))
    meta = json.loads((SAMPLES / "meta.json").read_text())
    if n is None:
        return meta, (SAMPLES / "unknown.txt").read_text().splitlines(), {}
    if n > meta["size"]:
        sys.exit(f"Bộ key chỉ có {meta['size']} key, cần {n}: chạy make sample SIZE={n}")
    lookups = (SAMPLES / "lookups.txt").read_text().splitlines()[:n]
    wanted = {line.rsplit(",", 1)[0] for line in lookups}
    expected = {}
    with open(SAMPLES / "expected.jsonl", encoding="utf-8") as f:
        for line in f:
            ref = json.loads(line)["reference"]
            if ref in wanted:
                expected[ref] = line.rstrip("\n")
    return meta, lookups, expected


def integrity(result_file, expected):
    """Số bản ghi trả về khớp từng byte với bản ghi lấy từ Oracle."""
    matched = 0
    with open(result_file, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if expected.get(json.loads(line)["reference"]) == line:
                matched += 1
    return matched


def stem(args):
    """Tên file kết quả: dump, find-<N>, find-unknown (+ -cold khi đo cache lạnh)."""
    name = f"find-{args.n}" if args.op == "find" else args.op
    return name + ("-cold" if args.op != "dump" and args.cache == "cold" else "")


def run(args):
    impls = known_impls()
    names = select(impls, args.impl)
    variant = f"-{args.label}" if args.label else ""
    meta, lookups, expected = (None, [], {}) if args.op == "dump" else load_sample(args.n if args.op == "find" else None)
    written, wrong = [], []
    with tempfile.TemporaryDirectory(prefix="bench-") as tmp:
        refs = Path(tmp) / "lookups.txt"
        refs.write_text("\n".join(lookups) + "\n")
        for name in names:
            lang, option, ext = impls[name]
            archive = DATA / f"{name}-{MODE}{variant}"  # thư mục, mỗi ngày một file
            out_dir = RESULTS / MODE / f"{name}{variant}"
            if args.op != "dump" and not any(archive.glob(f"*.{ext}")):
                sys.exit(f"Chưa có {archive.relative_to(ROOT)}, chạy make dump IMPL={name} SEED_MODE={MODE} trước")
            if args.op != "dump" and args.cache == "warm":
                run_once(lang, option, archive, refs, out_dir, args, 0, expected)  # làm nóng, không ghi
            runs = []
            for i in range(1, args.runs + 1):
                if args.op != "dump" and args.cache == "cold":
                    drop_caches()
                runs.append(run_once(lang, option, archive, refs, out_dir, args, i, expected))
            path = write_result(out_dir, name, args, runs, archive, meta)
            written.append(path)
            if not all(r["ok"] for r in runs):
                wrong.append(path)
    print("\nĐã ghi kết quả:\n" + "\n".join(f"  {p.relative_to(ROOT)}" for p in written))
    print("Vẽ báo cáo: make summary")
    if wrong:
        sys.exit("Có lần chạy KHÔNG đúng (ok=false), xem thư mục logs/ cạnh các file trên")


def run_once(lang, option, archive, refs, out_dir, args, run, expected):
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    op = "dump" if args.op == "dump" else "find"
    command = [*LANGUAGES[lang](), op, "--impl", option, "--archive", str(archive)]
    tmp = Path(tempfile.mkdtemp(prefix="bench-"))  # kết quả FIND chỉ để so rồi xoá
    if op == "find":
        command += ["--refs", str(refs), "--out", str(tmp / "result.jsonl")]
    print(f"→ {lang}-{option}{'-' + args.label if args.label else ''} {MODE} {stem(args)} "
          f"{'(làm nóng)' if run == 0 else f'lần {run}/{args.runs}'} ...", flush=True)
    try:
        code, wall, cpu, rss, last = measure(command, logs / f"{stem(args)}-{run}.log")
        result = json.loads(last) if code == 0 and last.startswith("{") else {}
        matched = integrity(tmp / "result.jsonl", expected) if op == "find" and code == 0 else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if args.op == "dump":
        ok = code == 0 and result.get("rows", 0) > 0
    elif args.op == "find":
        ok = code == 0 and result.get("found") == args.n and matched == args.n
    else:
        ok = code == 0 and result.get("found") == 0
    measured = {
        "run": run, "wall_s": round(wall, 3), "cpu_s": round(cpu, 3), "peak_rss_mb": round(rss),
        "file_mb": round(archive_size(archive) / 2**20) if args.op == "dump" else None,
        "rows": result.get("rows"), "found": result.get("found"),
        "expected": args.n if args.op == "find" else (0 if args.op == "find-unknown" else None),
        "checked": args.n if args.op == "find" else None, "matched": matched if args.op == "find" else None,
        "ok": ok,
    }
    file_note = f" · file {measured['file_mb']} MB" if measured["file_mb"] is not None else ""
    check_note = f" · khớp Oracle {matched}/{args.n}" if args.op == "find" else ""
    print(f"  wall {wall:.3f} s · cpu {cpu:.3f} s · RAM {rss:.0f} MB{file_note}{check_note} · {'OK' if ok else 'SAI'}")
    return measured


def write_result(out_dir, name, args, runs, archive, meta):
    """Một file cho mỗi (bộ dữ liệu, phương án, thao tác, cache); chạy lại thì ghi đè."""
    path = out_dir / f"{stem(args)}.json"
    last = runs[-1]
    result = {
        "dataset": MODE, "impl": name, "variant": args.label, "op": args.op,
        "n": args.n if args.op == "find" else None,
        "cache": args.cache if args.op != "dump" else None, "repeat": REPEAT,
        "sample": meta["id"] if meta else None,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"), "machine": machine(), "settings": settings(args.op),
        "archive": str(archive.relative_to(ROOT)),
        # Thời gian lấy trung vị, RAM lấy lớn nhất qua các lần chạy; khớp Oracle lấy lần tệ nhất.
        "summary": {
            "runs": len(runs),
            "wall_s": statistics.median(r["wall_s"] for r in runs),
            "cpu_s": statistics.median(r["cpu_s"] for r in runs),
            "peak_rss_mb": max(r["peak_rss_mb"] for r in runs),
            "file_mb": last["file_mb"], "rows": last["rows"], "found": last["found"], "expected": last["expected"],
            "checked": last["checked"],
            "matched": min(r["matched"] for r in runs) if args.op == "find" else None,
            "ok": all(r["ok"] for r in runs),
        },
        "runs": runs,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return path


def summary():
    """Đọc results/ và vẽ final.html (3 candidate, một câu SELECT mỗi ngày). Ô nào chưa có kết quả ghi "đang đo"."""
    import final_html  # đọc results/ lúc import, nên import ở đây để lấy số mới nhất
    import upgrade_html  # thử nén RocksDB tốt hơn, file riêng, không đụng final.html
    for module in (final_html, upgrade_html):
        path, missing = module.write()
        print(f"{path.relative_to(ROOT)}  ← mở bằng trình duyệt" + (f" · còn {len(missing)} ô đang đo" if missing else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="op", required=True)
    sub.add_parser("list")
    sub.add_parser("summary")
    sub.add_parser("sample").add_argument("--size", type=int, default=1000)
    for op in ("dump", "find", "find-unknown"):
        p = sub.add_parser(op)
        p.add_argument("--impl", required=True)
        p.add_argument("--runs", type=int, default=1 if op == "dump" else 3)
        p.add_argument("--label", default="", help="tên cấu hình, ví dụ 2m; tách archive và kết quả")
        p.add_argument("--cache", choices=["warm", "cold"], default="warm")
        if op == "find":
            p.add_argument("--n", type=int, required=True, help="số key tra, lấy N key đầu của bộ đã bốc")
    args = parser.parse_args()
    if args.op == "list":
        for name, (_, _, ext) in known_impls().items():
            print(f"{name}\t.{ext}")
    elif args.op == "summary":
        summary()
    elif args.op == "sample":
        sample(args.size)
    else:
        run(args)


if __name__ == "__main__":
    main()
