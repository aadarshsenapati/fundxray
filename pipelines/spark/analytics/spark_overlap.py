"""Industry-scale overlap on Spark.

The pandas implementation in metrics.py is correct and fine for one portfolio.
At 10,000 schemes the pairwise problem is O(n^2) over hundreds of millions of
rows and must be distributed.

Skew warning: a handful of Nifty names appear in nearly every scheme, so the
join key distribution is extreme. See docs/benchmarks.md for the diagnosis and
the salting vs. AQE comparison.
"""
from __future__ import annotations

import argparse


def build_session(app: str = "fundxray-overlap"):
    from pyspark.sql import SparkSession
    return (SparkSession.builder.appName(app)
            .config("spark.sql.adaptive.enabled", "true")
            .config("spark.sql.adaptive.skewJoin.enabled", "true")
            .config("spark.sql.shuffle.partitions", "200")
            .getOrCreate())


def compute_overlap(spark, holdings_path: str, out_path: str, salt_buckets: int = 0):
    from pyspark.sql import functions as F

    h = (spark.read.parquet(holdings_path)
         .filter((F.col("asset_class") == "equity") & F.col("company_id").isNotNull())
         .select("scheme_code", "company_id", "weight_pct", "disclosure_month"))
    latest = h.agg(F.max("disclosure_month")).collect()[0][0]
    h = h.filter(F.col("disclosure_month") == latest)

    a = h.selectExpr("scheme_code as s1", "company_id", "weight_pct as w1")
    b = h.selectExpr("scheme_code as s2", "company_id", "weight_pct as w2")

    if salt_buckets > 0:
        # Salting: break hot keys (mega caps) across buckets so no single task
        # receives the whole distribution.
        a = a.withColumn("salt", (F.rand() * salt_buckets).cast("int"))
        b = b.withColumn("salt", F.explode(F.array(*[F.lit(i) for i in range(salt_buckets)])))
        joined = a.join(b, ["company_id", "salt"])
    else:
        joined = a.join(b, "company_id")

    res = (joined.filter(F.col("s1") < F.col("s2"))
           .withColumn("m", F.least("w1", "w2"))
           .groupBy("s1", "s2").agg(F.sum("m").alias("overlap_pct")))
    res.write.mode("overwrite").parquet(out_path)
    return res


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--holdings", default="data/warehouse/silver_holdings.parquet")
    p.add_argument("--out", default="data/warehouse/mart_overlap")
    p.add_argument("--salt", type=int, default=0, help="salt buckets; 0 = rely on AQE")
    args = p.parse_args()
    spark = build_session()
    compute_overlap(spark, args.holdings, args.out, args.salt)
    spark.stop()
