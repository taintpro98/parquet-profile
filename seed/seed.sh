#!/usr/bin/env bash
# Dữ liệu bench thật, sinh ngẫu nhiên vào Oracle chạy trong Docker.
#
#   seed/seed.sh              sinh 100k dòng vào bảng của SEED_MODE (random | status),
#                             rồi ghi dung lượng bảng và index vào results/<SEED_MODE>/oracle.json.
#                             Bảng đã có dữ liệu thì không sinh lại; muốn sinh mới phải xoá trước.
#   seed/seed.sh clean        xoá Oracle (container + data/oracle), mọi archive trong data/ và bộ key
#                             trong bench/samples. Giữ nguyên results/.
set -euo pipefail
cd "$(dirname "$0")/.."

CONTAINER=parquet-profile-oracle
APP="docker exec -i $CONTAINER sqlplus -s benchmark/benchmark@//localhost:1521/FREEPDB1"
MODE=${SEED_MODE:-random}
TABLE=${ORACLE_TABLE:?ORACLE_TABLE chưa đặt, chạy qua make}

query() {
  printf 'SET HEADING OFF FEEDBACK OFF PAGESIZE 0\n%s\nEXIT\n' "$1" | $APP | tr -d '[:space:]'
}

table_exists() {
  [ "$(query "SELECT COUNT(*) FROM user_tables WHERE table_name = '$1';")" != 0 ]
}

oracle() {
  echo "== Oracle (Docker)"
  docker compose up -d --wait
}

seed() {
  oracle
  if table_exists "$TABLE"; then
    echo "== Đã có dữ liệu $MODE: $TABLE $(query "SELECT COUNT(*) FROM $TABLE;") dòng. Không sinh lại."
    echo "   Muốn sinh mới: make seed-clean rồi make seed SEED_MODE=$MODE"
  else
    echo "== Sinh 100.000 dòng ($MODE) vào $TABLE"
    go -C seed/gen build -o gen .
    seed/gen/gen
  fi
  oracle_size
}

# Dung lượng thật của bảng trong Oracle (segment đã cấp phát), riêng bảng và riêng các index.
oracle_size() {
  local rows table_mb index_mb file="results/$MODE/oracle.json"
  rows=$(query "SELECT COUNT(*) FROM $TABLE;")
  table_mb=$(query "SELECT TO_CHAR(ROUND(SUM(bytes)/1048576, 1), 'FM999999990.0') FROM user_segments WHERE segment_name = '$TABLE';")
  index_mb=$(query "SELECT TO_CHAR(ROUND(NVL(SUM(s.bytes), 0)/1048576, 1), 'FM999999990.0') FROM user_segments s JOIN user_indexes i ON i.index_name = s.segment_name WHERE i.table_name = '$TABLE';")
  mkdir -p "results/$MODE"
  printf '{\n  "dataset": "%s",\n  "table": "%s",\n  "rows": %s,\n  "table_mb": %s,\n  "index_mb": %s,\n  "time": "%s"\n}\n' \
    "$MODE" "$TABLE" "$rows" "$table_mb" "$index_mb" "$(date '+%Y-%m-%d %H:%M')" > "$file"
  echo "== Oracle $TABLE: $rows dòng, bảng $table_mb MB, index $index_mb MB → $file"
}

clean() {
  echo "Sẽ xoá:"
  du -sh data/* 2>/dev/null | sed 's/^/  /' || true
  [ -d bench/samples ] && echo "  bench/samples/"
  if [ "${YES:-}" != 1 ]; then
    read -r -p "Gõ yes để xoá: " answer
    [ "$answer" = yes ] || { echo "Huỷ."; exit 1; }
  fi
  docker compose down
  rm -rf data
  rm -rf bench/samples
  echo "Đã xoá. Sinh lại: make seed"
}

case "${1:-}" in
  "") seed ;;
  clean) clean ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
