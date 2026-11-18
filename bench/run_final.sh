#!/bin/bash
# Chạy lại 3 ứng viên trên CÙNG bộ dữ liệu, CÙNG phiên, CÙNG giao thức đo (dump 3 lần lấy trung vị, rồi tra
# 1 key / 1000 key / key không tồn tại), mỗi ứng viên một cấu hình tốt nhất. Chạy từ thư mục gốc:
#
#   bench/run_final.sh screen          chọn cấu hình: mỗi cấu hình dump 1 lần + tra, nhãn screen-*
#   bench/run_final.sh extra           bổ sung: go-rocksdb không dictionary (so cùng dữ liệu), Parquet 2M dòng
#   ROCKS_BEST=d16|d64 PARQUET_BEST="KEY=VAL ..." bench/run_final.sh final
#                                      đo chính thức 3 ứng viên, nhãn final
#
# Cấu hình là biến môi trường của từng phương án (xem docstring của RocksdbStore, rocksdb.go, ParquetDuckdb).
# Dữ liệu: make seed với SEED cố định (ghi ở results/status/seed.json), lô đọc Oracle 1.000 cho cả ba.
cd "$(dirname "$0")/.." || exit 1
export SEED_MODE=status ORACLE_REPEAT=10 ORACLE_REPEAT_SINGLE=true ORACLE_FETCH_SIZE=1000

# ZSTD mức 3. D16: tham số sếp đề xuất. D64: dictionary 64 KB, mẫu train 100x dictionary.
D16="ROCKSDB_ZSTD_LEVEL=3 ROCKSDB_ZSTD_DICT_BYTES=16384 ROCKSDB_ZSTD_TRAIN_BYTES=2097152 ROCKSDB_ZSTD_BUFFER_BYTES=16777216"
D64="ROCKSDB_ZSTD_LEVEL=3 ROCKSDB_ZSTD_DICT_BYTES=65536 ROCKSDB_ZSTD_TRAIN_BYTES=6553600 ROCKSDB_ZSTD_BUFFER_BYTES=67108864"

# Ghi tham số native của SST (sst_dump) và việc file có khối dictionary hay không.
sst_check() {
  local res=$1 arc=$2
  command -v rocksdb_sst_dump >/dev/null || return 0
  rm -f "$res/sst_check.txt"
  for sst in "$arc"/*.rocksdb/*.sst; do
    {
      echo "file: $sst"
      rocksdb_sst_dump --file="$sst" --show_properties 2>&1 | grep -E "compression algo|compression options"
      printf "khối rocksdb.compression_dict: "
      tail -c 2000000 "$sst" | LC_ALL=C grep -a -c "rocksdb.compression_dict"
    } >> "$res/sst_check.txt"
  done
}

# dump_case IMPL LABEL RUNS [KEY=VAL...]: dump rồi kiểm tra SST (giữ archive để tra).
dump_case() {
  local impl=$1 label=$2 runs=$3; shift 3
  echo "=== dump $impl $label runs=$runs ($*) $(date +%H:%M)"
  env "$@" make dump IMPL=$impl LABEL=$label RUNS=$runs 2>&1 | grep -E "lần|wall|Error|Exception|Traceback|panic"
  mkdir -p "results/status/$impl-$label"
  case $impl in *rocksdb*) sst_check "results/status/$impl-$label" "data/$impl-status-$label";; esac
}

# find_case IMPL LABEL [KEY=VAL...]: tra 1 key, 1000 key, key không tồn tại trên archive của LABEL.
find_case() {
  local impl=$1 label=$2; shift 2
  echo "=== find $impl $label ($*) $(date +%H:%M)"
  for op in "find N=1" "find N=1000" "find-unknown"; do
    env "$@" make $op IMPL=$impl LABEL=$label 2>&1 | grep -E "lần 3|Error|Traceback|panic"
  done
}

drop() { rm -rf "data/$1-status-$2"; }

case "$1" in
screen)
  for spec in "d16:$D16" "d64:$D64"; do
    l=screen-${spec%%:*}; e=${spec#*:}
    dump_case go-rocksdb $l 1 $e; find_case go-rocksdb $l $e; drop go-rocksdb $l
  done
  # Parquet: codec ZSTD (mặc định) và SNAPPY; DuckDB 4 luồng (mặc định) và 10 luồng trên cùng archive ZSTD.
  dump_case java-parquet-duckdb screen-zstd 1 PARQUET_COMPRESSION=ZSTD
  find_case java-parquet-duckdb screen-zstd DUCKDB_THREADS=4
  ln -s "$PWD/data/java-parquet-duckdb-status-screen-zstd" data/java-parquet-duckdb-status-screen-zstd-t10
  find_case java-parquet-duckdb screen-zstd-t10 DUCKDB_THREADS=10
  rm -f data/java-parquet-duckdb-status-screen-zstd-t10; drop java-parquet-duckdb screen-zstd
  dump_case java-parquet-duckdb screen-snappy 1 PARQUET_COMPRESSION=SNAPPY
  find_case java-parquet-duckdb screen-snappy DUCKDB_THREADS=4
  drop java-parquet-duckdb screen-snappy
  ;;
screen-java)
  # Chọn cấu hình riêng cho java-rocksdb: tắt dictionary, d16, d64, mỗi cấu hình dump 1 lần + tra.
  for spec in "off:ROCKSDB_ZSTD_LEVEL=3" "d16:$D16" "d64:$D64"; do
    l=screen-${spec%%:*}; e=${spec#*:}
    dump_case java-rocksdb $l 1 $e; find_case java-rocksdb $l $e; drop java-rocksdb $l
  done
  ;;
screen-fetch)
  # Lô đọc Oracle: 1000 đã có ở screen-d64; thử thêm 5000 và 10000 (chỉ ảnh hưởng dump), cấu hình d64.
  for f in 5000 10000; do
    for impl in java-rocksdb go-rocksdb; do
      dump_case $impl screen-fetch$f 1 $D64 ORACLE_FETCH_SIZE=$f; drop $impl screen-fetch$f
    done
  done
  ;;
final)
  : "${ROCKS_BEST:?đặt ROCKS_BEST=d16 hoặc d64}" "${PARQUET_BEST:?đặt PARQUET_BEST=KEY=VAL...}"
  case $ROCKS_BEST in d16) R=$D16;; d64) R=$D64;; *) echo "ROCKS_BEST phải là d16 hoặc d64"; exit 1;; esac
  for impl in go-rocksdb java-rocksdb; do
    dump_case $impl final 3 $R; find_case $impl final $R; drop $impl final
  done
  dump_case java-parquet-duckdb final 3 $PARQUET_BEST; find_case java-parquet-duckdb final $PARQUET_BEST
  drop java-parquet-duckdb final
  ;;
extra)
  # Không dictionary: đối chứng cho mức nén của dictionary, cùng dữ liệu và cùng giao thức bước chọn cấu hình.
  dump_case go-rocksdb screen-off 1 ROCKSDB_ZSTD_LEVEL=3; find_case go-rocksdb screen-off ROCKSDB_ZSTD_LEVEL=3
  drop go-rocksdb screen-off
  # Parquet 2M dòng (ORACLE_REPEAT=20): tra 1000 key có tăng theo kích thước file không.
  dump_case java-parquet-duckdb final-2m 1 ORACLE_REPEAT=20 PARQUET_COMPRESSION=ZSTD DUCKDB_THREADS=4
  find_case java-parquet-duckdb final-2m ORACLE_REPEAT=20 PARQUET_COMPRESSION=ZSTD DUCKDB_THREADS=4
  drop java-parquet-duckdb final-2m
  ;;
*) echo "dùng: bench/run_final.sh screen | final | extra"; exit 1 ;;
esac
echo "ALL_DONE $(date +%H:%M)"
