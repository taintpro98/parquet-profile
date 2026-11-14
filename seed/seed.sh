#!/usr/bin/env bash
# Dữ liệu bench thật, sinh ngẫu nhiên vào Oracle chạy trong Docker.
#
#   seed/seed.sh              sinh 100k dòng ngẫu nhiên vào ARCHIVE_PROFILE_DATA + bảng golden.
#                             Đã có dữ liệu thì không làm gì; muốn sinh mới phải xoá trước.
#   seed/seed.sh clean        xoá toàn bộ dữ liệu cũ để giải phóng ổ đĩa: Oracle (container +
#                             data/oracle), các archive trong data/, bench/refs sinh kèm.
#   seed/seed.sh archives [IMPL]
#                             dump archive một ngày (ORACLE_REPEAT lượt × 100k, fetch 10000) vào data/<impl>/
set -euo pipefail
cd "$(dirname "$0")/.."

CONTAINER=parquet-profile-oracle
APP="docker exec -i $CONTAINER sqlplus -s benchmark/benchmark@//localhost:1521/FREEPDB1"
REFS="bench/refs/one.txt bench/refs/many-1000.txt bench/refs/missing.txt"

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
  if table_exists ARCHIVE_PROFILE_DATA; then
    echo "== Đã có dữ liệu: ARCHIVE_PROFILE_DATA $(query "SELECT COUNT(*) FROM ARCHIVE_PROFILE_DATA;") dòng. Không làm gì."
    echo "   Muốn sinh mới: make seed-clean rồi make seed"
  else
    echo "== Sinh 100.000 dòng ngẫu nhiên vào ARCHIVE_PROFILE_DATA"
    go -C seed/gen build -o gen .
    seed/gen/gen
    echo "   $(query "SELECT COUNT(*) FROM ARCHIVE_PROFILE_DATA;") dòng, $(query "SELECT ROUND(SUM(bytes)/1048576) FROM user_segments;") MB trong Oracle"
  fi
  if ! table_exists ARCHIVE_GOLDEN; then
    echo "== Bảng golden ARCHIVE_GOLDEN (cho make test)"
    $APP < testdata/golden.sql
  fi
}

clean() {
  echo "Sẽ xoá:"
  du -sh data/* 2>/dev/null | sed 's/^/  /' || true
  for f in $REFS; do [ -f "$f" ] && echo "  $f"; done
  if [ "${YES:-}" != 1 ]; then
    read -r -p "Gõ yes để xoá: " answer
    [ "$answer" = yes ] || { echo "Huỷ."; exit 1; }
  fi
  docker compose down
  rm -rf data
  rm -f $REFS
  echo "Đã xoá. Sinh lại: make seed"
}

archives() {
  [ -f bench/refs/one.txt ] || { echo "Chưa có dữ liệu, chạy make seed trước." >&2; exit 1; }
  oracle
  echo "== Archive 10M cho ${1:-all}"
  make dump IMPL="${1:-all}" ORACLE_FETCH_SIZE=10000
}

case "${1:-}" in
  "") seed ;;
  clean) clean ;;
  archives) archives "${2:-all}" ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
