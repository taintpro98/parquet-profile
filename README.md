# parquet-profile

## Purpose

A small benchmark for a cold archive design. It answers one question: what CPU time and peak RAM
does a Java process need for these two operations?

- **WRITE:** Oracle 10M rows → one Parquet file sorted by `REFERENCE`
- **FIND:** Parquet → DuckDB → one record by `REFERENCE`

One invocation runs one operation. It is not a benchmark suite and it does not generate test data:
the Oracle table must already exist.

## Metrics

| Metric | Meaning |
| --- | --- |
| **Wall Time** | Elapsed time from `System.nanoTime()`. Includes waiting for Oracle, the network, the disk and the OS scheduler. |
| **CPU Time** | `OperatingSystemMXBean.getProcessCpuTime()` delta: CPU consumed by all threads of the Java process, including GC, JIT, the sampler and DuckDB's native threads. Time spent waiting is not counted, so it can be lower than Wall Time. On many cores it can also be higher. |
| **Peak CPU** | Highest CPU use between two samples, about 100 ms apart, in cores. A short burst inside one interval is averaged with the rest of that interval. |
| **Peak Process RAM** | The primary RAM metric. Highest resident set size (RSS) of the whole process, including heap, metaspace, thread stacks and native memory. |
| **Peak JVM Heap** | Context only. Highest used heap, including uncollected garbage. It does not contain DuckDB's native memory. |
| **File Size** | Size of the finished Parquet file (WRITE only). |

RSS has no portable Java API, so `MemorySampler` is OS specific. A sample is taken every 100 ms.

| OS | Source | Notes |
| --- | --- | --- |
| Linux | `/proc/self/status` | `VmRSS` samples plus the kernel's `VmHWM` high-water mark, so short peaks between samples are not missed. Preferred for real numbers. |
| macOS | `ps -o rss= -p <pid>` | Spawns a process per sample. The spawn adds a little CPU Time, and peaks shorter than 100 ms can be missed, which matters for FIND. |
| Other | none | Prints `n/a`. |

## Build

Requires Java 25 (the current LTS release) and Maven.

```bash
mvn clean package
```

## WRITE

```bash
java -cp target/parquet-profile.jar flows/Dump.java
```

```text
=== DUMP ===
Source           : Oracle
Output           : <absolute path>/data/transactions.parquet
Rows             : <rows>
Wall Time        : <seconds>
CPU Time         : <seconds>
Peak CPU         : <cores>
Peak Process RAM : <size>
Peak JVM Heap    : <size>
File Size        : <size>
Max Heap         : <size>
Fetch Size       : 1,000
Table Passes     : 1
Chart            : <path>
```

Progress is printed every 500,000 rows. The file is written to `transactions.parquet.tmp` and
renamed only after the writer closed successfully. A failed run deletes the temp file and leaves any
previous `transactions.parquet` untouched.

Set the heap for the row-group buffer, see [Row groups](#row-groups):

```bash
java -Xmx4g -cp target/parquet-profile.jar flows/Dump.java
```

Every successful WRITE also creates a chart `<name>-profile-<timestamp>.html` in the current
directory. It is one self-contained file showing CPU use and memory over time. Open it in a browser; hover to read values
at a moment, or expand the data table. Long runs are merged into at most 800 points: memory keeps the
highest value of each point, CPU both the highest and the average value, so peaks stay visible.

On macOS, RSS can drop below JVM heap while the process waits for Oracle, because the OS compresses
or swaps idle pages and `ps` stops counting them. That also means Peak Process RAM can read low on a
machine under memory pressure, so use Linux for RAM figures.

## FIND

FIND has two separate flows in `flows/`. They are outside `src/main/java`, so they run straight from
source without a build. The jar is only on the classpath for the shared classes and DuckDB. Changes
under `src/main/java` still need `mvn clean package`.

```bash
# Find one reference
java -Dparquet.output=data/transactions-10m.parquet -cp target/parquet-profile.jar \
     flows/FindOne.java TXN202610270052391827

# Find many: list them, or use @file with one reference per line
java -Dparquet.output=data/transactions-10m.parquet -cp target/parquet-profile.jar \
     flows/FindMany.java @refs.txt
```

`java File.java` compiles inside the measured process, which inflates Peak Process RAM and Peak JVM
Heap. For memory figures compile first:

```bash
javac -cp target/parquet-profile.jar -d target/flows flows/*.java
java -Dparquet.output=data/transactions-10m.parquet -cp target/flows:target/parquet-profile.jar \
     FindOne TXN202610270052391827
```

FindMany runs one `WHERE reference IN (...)` query for all references, so there is no time per
reference; Per Reference is Wall Time divided by the reference count. Results are printed for up to 20
references; the chart always lists them all.

### Memory

Peak Process RAM of FIND is mostly DuckDB decompressing wide text columns. These properties control it.

| Property | Default | Effect |
| --- | --- | --- |
| `duckdb.threads` | `4` | Fewer threads use less memory and take longer. DuckDB's own default is one per core. |
| `duckdb.external-file-cache` | `false` | DuckDB's cache of compressed file blocks. It can help repeated queries on one connection. |

FIND always reads every column, because the archive must restore complete records.

`SET memory_limit` is not a fix: the query needs this memory and fails with Out of Memory below it.

Each run creates `<name>-find-one-profile-<timestamp>.html` or
`<name>-find-many-<count>-profile-<timestamp>.html` in the current directory, with the lookup results
and the CPU and memory timeline. A `.jsonl` file with the same name holds the complete records, one
JSON object per line with every column in file order and `null` for NULL. The chart and FindMany's
terminal output show only a few columns; FindOne prints every column. Wall Time, CPU Time and RAM
cover reading the records into memory, not writing this file, which FindMany reports as Export Time. Samples are taken every 100 ms, so a short run shows only a couple of
timeline points.

The measured section covers preparing and running the query and reading every column of the match.
Opening DuckDB happens before it, because loading the native library from the jar costs far more
than the lookup itself. Wall Time and CPU Time therefore exclude that startup, but Peak Process RAM
still includes it because RSS covers the whole process.

## Local Oracle for testing

`docker-compose.yml` starts Oracle Free with 100,000 generated rows from `docker/seed.sql`. It is for
smoke tests, not for benchmark numbers. It needs about 6 GB on the host for `data/oracle`.

```bash
docker compose up -d
docker compose logs -f oracle   # wait for "DATABASE IS READY TO USE"

java -Dparquet.row-group-rows=20000 -cp target/parquet-profile.jar flows/Dump.java
java -cp target/parquet-profile.jar flows/FindOne.java TXN000000023757

docker compose down             # keeps data/oracle; delete it to re-seed
```

### 10M rows by re-reading a smaller table

`oracle.repeat` reads the whole table that many times over one connection and writes all passes into
one Parquet file. 100 passes over the 100,000 seeded rows give 10,000,000 rows, each one really read
through JDBC, with Oracle running the query and `ORDER BY REFERENCE` on every pass.

```bash
java -Xmx4g -Doracle.repeat=100 \
     -Dparquet.output=data/transactions-10m.parquet \
     -cp target/parquet-profile.jar flows/Dump.java

java -Dparquet.output=data/transactions-10m.parquet -cp target/parquet-profile.jar \
     flows/FindOne.java 0050TXN000000023757
```

To keep the file sorted and unique, the SQL prefixes `REFERENCE` with the 4-digit pass number and
adds `pass * 1,000,000,000` to `ID`, so `REFERENCE` is 4 characters longer than the source value.
Java still does not sort. The table's `ID` values must stay below that step.

Limits of this simulation:

- After the first pass Oracle serves blocks from its buffer cache, and it sorts 100,000 rows many
  times rather than 10,000,000 once. Wall Time is therefore not what a real 10M table would show.
- Row groups contain the same text repeatedly, so size and ZSTD CPU reflect the seeded data only.

### 10M rows without storing them

`docker/generator.sql` creates the view `ARCHIVE_PROFILE_GEN`, which generates 10,000,000 rows at
query time. Oracle stores nothing, and the rows arrive in `REFERENCE` order without a sort, so the
only disk needed is for the Parquet file.

```bash
docker exec -i parquet-profile-oracle sqlplus -s benchmark/benchmark@//localhost:1521/FREEPDB1 < docker/generator.sql

java -Xmx4g -Doracle.query='SELECT * FROM ARCHIVE_PROFILE_GEN' \
     -Dparquet.output=data/transactions-10m.parquet \
     -cp target/parquet-profile.jar flows/Dump.java
```

This measures the Java side at full scale, which is what CPU Time and Peak Process RAM report. It
does not represent Oracle read or sort time, and the generated text is repetitive, so real data
will compress worse: expect a larger file and more ZSTD CPU.

## Configuration

Defaults live in `src/main/resources/application.properties`. Any key can be overridden with
`-Dkey=value` without rebuilding.

| Property | Default | Meaning |
| --- | --- | --- |
| `oracle.url` | `jdbc:oracle:thin:@//localhost:1521/FREEPDB1` | JDBC URL, defaults to the local Docker Oracle |
| `oracle.username` / `oracle.password` | `benchmark` | Local test account, never commit real values |
| `oracle.table` | `ARCHIVE_PROFILE_DATA` | Source table |
| `oracle.query` | empty | Replaces the generated SELECT; same columns, in `REFERENCE` order |
| `oracle.repeat` | `1` | Reads the table this many times into one file, 1 to 9999 |
| `oracle.fetch-size` | `1000` | Rows per JDBC round trip |
| `oracle.text-columns` | `70` | Number of `TEXT_nn` columns |
| `parquet.output` | `data/transactions.parquet` | File written by WRITE and read by FIND |
| `parquet.compression` | `ZSTD` | Parquet codec name |
| `parquet.row-group-rows` | `250000` | Row-group row limit |
| `parquet.row-group-bytes` | `2147483648` | Row-group buffer limit |
| `progress.interval` | `500000` | Rows between progress lines |
| `duckdb.threads` | `4` | DuckDB threads for FIND |
| `duckdb.external-file-cache` | `false` | DuckDB file block cache for FIND |

Environment variables take priority for credentials:

```bash
export ORACLE_URL='jdbc:oracle:thin:@//db-host:1521/SERVICE'
export ORACLE_USERNAME='...'
export ORACLE_PASSWORD='...'
```

The SQL is built in `OracleReader.selectSql()` and the Parquet schema in
`ParquetArchiveWriter.schema()`. Change both together if the real table differs: columns are read by
position.

## Implementation decisions

### Streaming without row objects

`ParquetWriter<ResultSet>` uses a small custom `WriteSupport`, so the `ResultSet` itself is the
record type and `parquetWriter.write(resultSet)` copies the current row straight into Parquet's
column buffers. Avro and the example `Group` API were rejected because both would allocate a
temporary record per row. Nothing materializes the dataset. What stays in memory is the JDBC fetch
buffer, the current row's strings and the Parquet row-group buffer.

`LocalOutputFile` writes through `java.nio`, without Hadoop's filesystem layer. Hadoop remains only
because `parquet-hadoop` needs `Configuration` and its compression interfaces.

### Row groups

`ParquetWriter` flushes a row group when **either** limit is reached:

- `withRowGroupRowCountLimit` is a real row limit, checked on every record.
- `withRowGroupSize` is a byte limit on the writer's buffered size, checked only periodically. The
  size is an estimate, so row groups are not exactly this many bytes.

A row group stays on the Java heap until it is flushed, and the buffered pages are already
compressed. So 250,000 rows of 10 KB compressed data would need about 2.5 GB. The default 2 GiB
byte limit keeps that bounded, which means **wide rows can produce row groups smaller than 250,000
rows**. Raise `parquet.row-group-bytes` and `-Xmx` together if exactly 250,000 rows matters.

The default `java -jar` heap is a quarter of physical RAM. Peak JVM Heap and Peak Process RAM depend
directly on these settings, so report them with the results.

### Ordering

Oracle sorts; Java writes in arrival order and never re-sorts. `ALTER SESSION SET NLS_SORT = BINARY`
runs first because the thin driver derives the session language from the JVM locale, and some
languages imply a linguistic sort. Parquet min/max statistics use byte order, so a linguistic order
would make row-group ranges overlap.

### Types

| Oracle | Parquet |
| --- | --- |
| `ID NUMBER(19)` | required `INT64` |
| `REFERENCE VARCHAR2` | required `BINARY (STRING)` |
| `CREATED_AT TIMESTAMP` | required `INT64 (TIMESTAMP(MICROS, isAdjustedToUTC=false))` |
| `TEXT_nn VARCHAR2` | optional `BINARY (STRING)` |

`CREATED_AT` is read as `LocalDateTime`, so the JVM time zone cannot shift it. Fractional seconds
beyond microseconds are truncated. `ID` values above `Long.MAX_VALUE` fail the run rather than being
silently altered.

## Important limitations

- Oracle server CPU and RAM are **not** measured. CPU Time is the Java process only.
- Network time contributes to Wall Time but not directly to CPU Time.
- `ORDER BY REFERENCE` is performed by Oracle. Without a usable index on `REFERENCE`, Oracle sorts
  all wide rows in its TEMP tablespace before the first row arrives, which can dominate Wall Time
  and needs a lot of TEMP space.
- A long-running query needs enough undo retention, or it can fail with `ORA-01555`.
- JVM heap alone is insufficient for FIND because DuckDB allocates native memory. Use Peak Process RAM.
- RSS is measured for the whole process, so it includes the JVM's own baseline.
- Parquet row-group sizing is partly byte-based, see [Row groups](#row-groups).
- FIND results are affected by the OS filesystem cache, DuckDB initialization, JVM warmup and JIT.
  Repeated runs may represent warm-cache behavior, and there is no cache control here. Run the same
  lookup several times and compare references near the start, middle and end of the file by hand.
- WRITE needs free disk space for the whole Parquet file, as `.tmp` until it is renamed.
