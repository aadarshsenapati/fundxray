"""Performance lab — measured, not asserted.

Each benchmark states the problem, applies a fix, and records the delta.
Results land in benchmarks/results/ as JSON plus a markdown table.

Run:  python benchmarks/scripts/run_benchmarks.py --data /tmp/fx_bench/holdings
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime
from pathlib import Path

from pipelines.spark.session import build_session

RESULTS: list[dict] = []


def timed(name: str, group: str, fn, **meta):
    t0 = time.perf_counter()
    value = fn()
    secs = round(time.perf_counter() - t0, 2)
    row = {"group": group, "variant": name, "seconds": secs, "result": value, **meta}
    RESULTS.append(row)
    print(f"  {group:<22} {name:<28} {secs:>7.2f}s   {value}")
    return secs


# --- 1. skewed join -------------------------------------------------------
def overlap(spark, df, salt: int = 0):
    from pyspark.sql import functions as F

    a = df.selectExpr("scheme_code as s1", "company_id", "weight_pct as w1")
    b = df.selectExpr("scheme_code as s2", "company_id", "weight_pct as w2")
    if salt:
        a = a.withColumn("salt", (F.rand(seed=11) * salt).cast("int"))
        b = b.withColumn("salt", F.explode(F.array(*[F.lit(i) for i in range(salt)])))
        j = a.join(b, ["company_id", "salt"])
    else:
        j = a.join(b, "company_id")
    return (j.filter(F.col("s1") < F.col("s2"))
             .withColumn("m", F.least("w1", "w2"))
             .groupBy("s1", "s2").agg(F.sum("m").alias("overlap_pct"))
             .count())


def bench_skew(data: str, schemes: int, shuffle: int):
    from pyspark.sql import functions as F

    print("\n[1] SKEWED JOIN — overlap mart")
    for label, aqe, salt in (("naive (no AQE, no salt)", False, 0),
                             ("AQE skew join", True, 0),
                             ("salted x8 (no AQE)", False, 8),
                             ("salted x8 + AQE", True, 8)):
        spark = build_session(f"bench-skew-{label}", shuffle_partitions=shuffle, aqe=aqe)
        df = (spark.read.parquet(data)
              .filter(F.col("scheme_code") < F.lit(f"S{schemes:05d}"))
              .filter(F.col("disclosure_month") == "2016-01-01").cache())
        rows = df.count()
        timed(label, "1-skewed-join", lambda: overlap(spark, df, salt),
              input_rows=rows, aqe=aqe, salt=salt, shuffle_partitions=shuffle)
        spark.stop()


# --- 2. partition pruning -------------------------------------------------
def bench_pruning(data: str):
    from pyspark.sql import functions as F

    print("\n[2] PARTITION PRUNING")
    spark = build_session("bench-pruning", shuffle_partitions=32)

    def full_scan():
        return spark.read.parquet(data).groupBy("company_id").sum("weight_pct").count()

    def pruned():
        return (spark.read.parquet(data)
                .filter(F.col("disclosure_month") == "2016-06-01")
                .groupBy("company_id").sum("weight_pct").count())

    timed("full scan (24 months)", "2-partition-pruning", full_scan)
    timed("pruned to 1 month", "2-partition-pruning", pruned)
    spark.stop()


# --- 3. small files -------------------------------------------------------
def bench_small_files(data: str, tmp: str, sample: float = 1.0):
    print("\n[3] SMALL FILES vs COMPACTED")
    spark = build_session("bench-smallfiles", shuffle_partitions=32)
    # Sampled so the lab completes on a 2-core box. Scale the sample up on real
    # hardware; the RATIO is what this benchmark measures, not absolute time.
    src = spark.read.parquet(data).sample(fraction=sample, seed=3).cache()
    print(f"  (small-files benchmark on {src.count():,} sampled rows)")

    many, few = f"{tmp}/many_small", f"{tmp}/compacted"
    src.repartition(200).write.mode("overwrite").parquet(many)
    src.coalesce(2).write.mode("overwrite").parquet(few)

    def count_files(p):
        return sum(1 for f in Path(p).rglob("*.parquet"))

    timed(f"200 small files ({count_files(many)} parts)", "3-small-files",
          lambda: spark.read.parquet(many).groupBy("company_id").count().count(),
          file_count=count_files(many))
    timed(f"compacted ({count_files(few)} parts)", "3-small-files",
          lambda: spark.read.parquet(few).groupBy("company_id").count().count(),
          file_count=count_files(few))
    spark.stop()


# --- 4. file formats ------------------------------------------------------
def bench_formats(data: str, tmp: str, sample: float = 1.0):
    print("\n[4] FILE FORMAT — size and scan time")
    spark = build_session("bench-formats", shuffle_partitions=32)
    src = spark.read.parquet(data).sample(fraction=sample, seed=3).cache()
    print(f"  (format benchmark on {src.count():,} sampled rows)")

    def size_mb(p):
        return round(sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file()) / 1e6, 1)

    for fmt in ("parquet", "orc"):
        out = f"{tmp}/fmt_{fmt}"
        src.write.mode("overwrite").format(fmt).save(out)
        timed(f"{fmt} scan+agg", "4-file-format",
              lambda o=out, f=fmt: spark.read.format(f).load(o)
              .groupBy("company_id").count().count(),
              size_on_disk_mb=size_mb(out))
    spark.stop()


# --- 5. shuffle partitions ------------------------------------------------
def bench_shuffle(data: str):
    print("\n[5] SHUFFLE PARTITIONS")
    for n in (8, 32, 200):
        spark = build_session(f"bench-shuffle-{n}", shuffle_partitions=n, aqe=False)
        df = spark.read.parquet(data)
        timed(f"spark.sql.shuffle.partitions={n}", "5-shuffle-partitions",
              lambda: df.groupBy("company_id", "scheme_code").sum("weight_pct").count(),
              shuffle_partitions=n)
        spark.stop()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="/tmp/fx_bench/holdings")
    p.add_argument("--tmp", default="/tmp/fx_bench/work")
    p.add_argument("--schemes", type=int, default=300, help="scheme cap for the join benchmark")
    p.add_argument("--shuffle", type=int, default=32)
    p.add_argument("--sample", type=float, default=1.0, help="sample fraction for the I/O benchmarks")
    p.add_argument("--only", default="", help="comma-separated benchmark numbers")
    a = p.parse_args()

    only = {s.strip() for s in a.only.split(",") if s.strip()}
    run = lambda n: (not only) or (n in only)

    print(f"Machine: {platform.processor() or platform.machine()} | "
          f"cores={__import__('os').cpu_count()} | {datetime.now():%Y-%m-%d %H:%M}")

    if run("1"):
        bench_skew(a.data, a.schemes, a.shuffle)
    if run("2"):
        bench_pruning(a.data)
    if run("3"):
        bench_small_files(a.data, a.tmp, a.sample)
    if run("4"):
        bench_formats(a.data, a.tmp, a.sample)
    if run("5"):
        bench_shuffle(a.data)

    out = Path("benchmarks/results")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    payload = {"generated_at": datetime.now().isoformat(),
               "cores": __import__("os").cpu_count(),
               "spark": "3.5.1", "results": RESULTS}
    (out / f"benchmarks_{stamp}.json").write_text(json.dumps(payload, indent=2))
    print(f"\nwrote benchmarks/results/benchmarks_{stamp}.json ({len(RESULTS)} measurements)")


if __name__ == "__main__":
    main()
