"""Generate a scale dataset with REALISTIC SKEW for the performance lab.

The skew is the point. In the real industry a handful of mega caps appear in
almost every scheme, so `company_id` — the join key for the overlap mart — has
an extremely heavy head. A uniformly random key would make the benchmarks
meaningless, because the pathology being measured would not exist.
"""
from __future__ import annotations

import argparse

from pipelines.spark.session import build_session


def generate(spark, n_schemes: int, n_months: int, avg_holdings: int, out: str):
    from pyspark.sql import functions as F

    n_companies = 2000
    total = n_schemes * n_months * avg_holdings

    base = spark.range(0, total).withColumn("r", F.rand(seed=42))

    # Zipf-like key distribution: ~35% of all rows land on the top 20 companies.
    company = (F.when(F.col("r") < 0.35, (F.rand(seed=1) * 20).cast("int"))
                .when(F.col("r") < 0.70, (F.rand(seed=2) * 200).cast("int"))
                .otherwise((F.rand(seed=3) * n_companies).cast("int")))

    df = (base
          .withColumn("company_idx", company)
          .withColumn("company_id", F.concat(F.lit("INE"), F.lpad(F.col("company_idx").cast("string"), 6, "0")))
          .withColumn("scheme_code", F.concat(F.lit("S"), F.lpad(((F.col("id") % n_schemes)).cast("string"), 5, "0")))
          .withColumn("month_idx", ((F.col("id") / n_schemes) % n_months).cast("int"))
          .withColumn("disclosure_month", F.expr("add_months(to_date('2016-01-01'), month_idx)"))
          .withColumn("weight_pct", F.round(F.rand(seed=5) * 6, 4))
          .withColumn("asset_class", F.lit("equity"))
          .withColumn("market_value", F.round(F.rand(seed=6) * 1e9, 2))
          .drop("r", "id", "company_idx", "month_idx"))

    df.write.mode("overwrite").partitionBy("disclosure_month").parquet(out)
    return total


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--schemes", type=int, default=600)
    p.add_argument("--months", type=int, default=24)
    p.add_argument("--avg-holdings", type=int, default=350)
    p.add_argument("--out", default="/tmp/fx_bench/holdings")
    a = p.parse_args()

    spark = build_session("fundxray-bench-gen", shuffle_partitions=32)
    n = generate(spark, a.schemes, a.months, a.avg_holdings, a.out)
    df = spark.read.parquet(a.out)
    print(f"GENERATED rows={df.count():,} schemes={df.select('scheme_code').distinct().count():,} "
          f"companies={df.select('company_id').distinct().count():,}")
    top = df.groupBy("company_id").count().orderBy("count", ascending=False).limit(5).collect()
    print("SKEW top-5 keys:", [(r["company_id"], r["count"]) for r in top])
    spark.stop()
