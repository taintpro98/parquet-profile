# Mọi lệnh chạy từ thư mục gốc. Chọn phương án bằng IMPL=<tên>[,<tên>...] hoặc IMPL=all.
#
#   make list                                      liệt kê phương án
#   make test   [IMPL=...]                         test đúng/sai với dữ liệu golden
#   make dump   IMPL=...  [RUNS=1] [LABEL=...]
#   make find   IMPL=...  [SCENARIO=all|one|many-1000|missing] [CACHE=warm|cold] [RUNS=5] [LABEL=...]
#   make bench  IMPL=...                           dump rồi find mọi kịch bản
#   make report                                    tạo lại results/REPORT.md
#   make seed                                      sinh 100k dòng ngẫu nhiên vào Oracle (Docker), chỉ khi chưa có
#   make seed-clean [YES=1]                        xoá dữ liệu cũ: Oracle, archive, refs
#   make seed-archives [IMPL=...]                  dump archive 10M để bench thật (lâu)
#   make db-down                                   tắt Oracle
#
# Cấu hình từng phương án qua biến môi trường, ví dụ:
#   make dump IMPL=go-parquet-duckdb PARQUET_ROW_GROUP_ROWS=50000 LABEL=rg50k

IMPL     ?= all
SCENARIO ?= all
CACHE    ?= warm
LABEL    ?=
RUNS     ?=

# make seed sinh bench/refs cho dữ liệu 10M = 100 lượt đọc bảng 100k dòng.
export ORACLE_REPEAT ?= 100

JAVA_HOME ?= $(or $(shell /usr/libexec/java_home -v 25 2>/dev/null),$(firstword $(wildcard $(HOME)/.jdk/jdk-25*/Contents/Home)))
MVN       ?= $(or $(shell command -v mvn),$(firstword $(wildcard $(HOME)/.m2/wrapper/dists/apache-maven-*/*/bin/mvn)))
export JAVA_HOME

JAR    := impl/java/target/archive.jar
GO_BIN := impl/go/bin/archive
BENCH  := python3 bench/bench.py
RUN_ARGS = --impl $(IMPL) $(if $(LABEL),--label $(LABEL)) $(if $(RUNS),--runs $(RUNS))

.PHONY: list build test dump find bench report seed seed-clean seed-archives db-down clean

list: build
	@$(BENCH) list

build: $(JAR) $(GO_BIN)

$(JAR): impl/java/pom.xml $(shell find impl/java/src/main -name '*.java')
	cd impl/java && $(MVN) -q package

$(GO_BIN): impl/go/go.mod $(shell find impl/go -name '*.go' ! -name '*_test.go')
	cd impl/go && go build -o bin/archive .

# Row group / lô / block nhỏ để file ngày của dữ liệu golden có nhiều row group, lô và block.
TEST_ENV := PARQUET_ROW_GROUP_ROWS=64 ARROW_BATCH_ROWS=16 BLOCK_BYTES=16384 ORACLE_REPEAT=1
test: build
	@if echo ",$(IMPL)," | grep -qE ',(all|java-[^,]*),'; then \
	  cd impl/java && IMPL=$(IMPL) $(TEST_ENV) $(MVN) -q test -DskipTests=false \
	  && echo "Java: PASS"; fi
	@if echo ",$(IMPL)," | grep -qE ',(all|go-[^,]*),'; then \
	  cd impl/go && IMPL=$(IMPL) $(TEST_ENV) go test -count=1 ./... \
	  && echo "Go: PASS"; fi

dump: build
	$(BENCH) dump $(RUN_ARGS)

find: build
	$(BENCH) find $(RUN_ARGS) --scenario $(SCENARIO) --cache $(CACHE)

bench: dump find

report:
	@$(BENCH) report

seed:
	seed/seed.sh

seed-clean:
	seed/seed.sh clean

seed-archives: build
	seed/seed.sh archives $(IMPL)

db-down:
	docker compose down

clean:
	rm -rf impl/java/target impl/go/bin
