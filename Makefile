# Mọi lệnh chạy từ thư mục gốc. Chọn phương án bằng IMPL=<tên>[,<tên>...] hoặc IMPL=all.
#
#   make seed [SEED_MODE=random|status]   sinh 100k dòng vào Oracle (Docker), chỉ khi chưa có
#   make sample [SIZE=1000]               bốc key ngẫu nhiên từ Oracle, dùng chung cho mọi phương án
#   make dump         IMPL=...            Oracle → 1 file ngày
#   make find N=1000  IMPL=...            tra N key đầu của bộ đã bốc, so từng cột với Oracle
#   make find-unknown IMPL=...            tra 1 key không tồn tại, phải trả rỗng
#   make bench        IMPL=...            dump + find N=1 + find N=1000 + find-unknown
#   make summary                          vẽ final.html và upgrade.html ở thư mục gốc từ results/
#   make list                             liệt kê phương án
#   make seed-clean [YES=1]               xoá Oracle và archive (giữ results/)
#   make db-down                          tắt Oracle
#
# Tuỳ chọn chung: SEED_MODE, ORACLE_REPEAT (10 = 1M dòng/ngày), RUNS, CACHE=warm|cold, LABEL.
# Cấu hình phương án qua biến môi trường, ví dụ:
#   make dump IMPL=go-parquet-duckdb PARQUET_ROW_GROUP_ROWS=50000 LABEL=rg50k

IMPL     ?= all
N        ?= 1000
SIZE     ?= 1000
CACHE    ?= warm
LABEL    ?=
RUNS     ?=

# Một ngày = ORACLE_REPEAT lượt đọc bảng 100k dòng: 10 = 1M, 20 = 2M (xem METRICS.md).
export ORACLE_REPEAT ?= 10
# Bộ dữ liệu: random (mọi cột text ngẫu nhiên) hoặc status (20% số cột là trạng thái lặp nhiều).
# Mỗi bộ một bảng Oracle, một bộ key và một thư mục archive riêng.
export SEED_MODE ?= random
export ORACLE_TABLE ?= ARCHIVE_$(shell echo $(SEED_MODE) | tr a-z A-Z)

# Ngân sách RAM của runtime, cùng một mức cho hai bên (pod 4 GiB, chừa phần còn lại cho bộ nhớ
# native như DuckDB, Arrow, zstd). Java: heap tối đa; Go: giới hạn mềm của bộ dọn rác.
export JAVA_OPTS ?= -Xmx2g
export GOMEMLIMIT ?= 2GiB
# Go đọc Oracle bằng godror (ODPI-C), cần Oracle Instant Client; ORACLE_DRIVER=go-ora để dùng driver Go thuần.
export ORACLE_CLIENT_LIB ?= $(HOME)/oracle/instantclient_23_3

JAVA_HOME ?= $(or $(shell /usr/libexec/java_home -v 25 2>/dev/null),$(firstword $(wildcard $(HOME)/.jdk/jdk-25*/Contents/Home)))
MVN       ?= $(or $(shell command -v mvn),$(firstword $(wildcard $(HOME)/.m2/wrapper/dists/apache-maven-*/*/bin/mvn)))
export JAVA_HOME

# grocksdb (go-rocksdb) cần cgo và librocksdb cài bằng Homebrew (brew install rocksdb).
BREW := $(shell brew --prefix 2>/dev/null)
export CGO_CFLAGS  ?= -I$(BREW)/include
export CGO_LDFLAGS ?= -L$(BREW)/lib -lrocksdb -lstdc++ -lm -lz -lsnappy -llz4 -lzstd -lbz2

JAR    := impl/java/target/archive.jar
GO_BIN := impl/go/bin/archive
BENCH  := python3 bench/bench.py
RUN_ARGS = --impl $(IMPL) $(if $(LABEL),--label $(LABEL)) $(if $(RUNS),--runs $(RUNS))

.PHONY: list build dump sample find find-unknown bench summary seed seed-clean db-down clean

list: build
	@$(BENCH) list

build: $(JAR) $(GO_BIN)

$(JAR): impl/java/pom.xml $(shell find impl/java/src/main -name '*.java')
	cd impl/java && $(MVN) -q package

$(GO_BIN): impl/go/go.mod $(shell find impl/go -name '*.go' ! -name '*_test.go')
	cd impl/go && go build -o bin/archive .

dump: build
	$(BENCH) dump $(RUN_ARGS)

sample: build
	$(BENCH) sample --size $(SIZE)

find: build
	$(BENCH) find $(RUN_ARGS) --n $(N) --cache $(CACHE)

find-unknown: build
	$(BENCH) find-unknown $(RUN_ARGS) --cache $(CACHE)

bench: build
	$(BENCH) dump $(RUN_ARGS)
	$(BENCH) find $(RUN_ARGS) --n 1 --cache $(CACHE)
	$(BENCH) find $(RUN_ARGS) --n 1000 --cache $(CACHE)
	$(BENCH) find-unknown $(RUN_ARGS) --cache $(CACHE)

summary:
	@$(BENCH) summary

seed:
	seed/seed.sh

seed-clean:
	seed/seed.sh clean


db-down:
	docker compose down

clean:
	rm -rf impl/java/target impl/go/bin
