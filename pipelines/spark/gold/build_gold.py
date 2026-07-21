"""Silver -> Gold analytical marts on Spark.

The expensive mart is the overlap matrix: pairwise across all schemes, which is
O(n^2) in schemes over hundreds of millions of holding rows. The join key is
company_id, and its distribution is extremely skewed — a handful of mega caps
appear in nearly every scheme in the country. Salting and AQE are both
implemented so the two can be compared; see docs/benchmarks.md for measurements.
"""
from __future__ import annotations

import argparse

from fundxray_core.utils.logging import get_logger
from pipelines.spark.session import build_session

log = get_logger(__name__)


def mart_overlap(spark, silver, salt_buckets: int = 0):
    from pyspark.sql import functions as F

    eq = (silver.filter((F.col("asset_class") == "equity") & F.col("company_id").isNotNull())
                .select("scheme_code", "company_id", "weight_pct", "disclosure_month"))
    latest = eq.agg(F.max("disclosure_month")).collect()[0][0]
    eq = eq.filter(F.col("disclosure_month") == latest).cache()

    a = eq.selectExpr("scheme_code as s1", "company_id", "weight_pct as w1")
    b = eq.selectExpr("scheme_code as s2", "company_id", "weight_pct as w2")

    if salt_buckets > 0:
        # Break hot keys across buckets so no single task inherits the whole
        # distribution of, say, HDFC Bank.
        a = a.withColumn("salt", (F.rand(seed=7) * salt_buckets).cast("int"))
        b = b.withColumn("salt", F.explode(F.array(*[F.lit(i) for i in range(salt_buckets)])))
        joined = a.join(b, ["company_id", "salt"])
    else:
        joined = a.join(b, "company_id")

    return (joined.filter(F.col("s1") < F.col("s2"))
                  .withColumn("m", F.least("w1", "w2"))
                  .groupBy("s1", "s2")
                  .agg(F.sum("m").alias("overlap_pct")))


def mart_crowding(spark, silver, schemes, universe):
    from pyspark.sql import functions as F

    eq = silver.filter(F.col("asset_class") == "equity")
    latest = eq.agg(F.max("disclosure_month")).collect()[0][0]
    eq = eq.filter(F.col("disclosure_month") == latest)

    j = eq.join(F.broadcast(schemes.select("scheme_code", "aum_cr")), "scheme_code", "left")
    val = j.withColumn("value_cr", F.col("weight_pct") / 100.0 * F.coalesce("aum_cr", F.lit(0.0)))
    agg = val.groupBy("company_id").agg(F.sum("value_cr").alias("mf_holding_cr"))
    return agg.join(F.broadcast(universe.select(
        F.col("isin").alias("company_id"), "company_name", "cap_bucket",
        "free_float_shares_cr")), "company_id", "inner")


def mart_style_drift(spark, silver, universe):
    from pyspark.sql import functions as F

    caps = universe.select(F.col("isin").alias("company_id"), "cap_bucket")
    eq = silver.filter(F.col("asset_class") == "equity").join(F.broadcast(caps), "company_id", "left")
    g = (eq.groupBy("scheme_code", "disclosure_month")
           .pivot("cap_bucket", ["large", "mid", "small"])
           .agg(F.sum("weight_pct")).na.fill(0.0))
    total = F.col("large") + F.col("mid") + F.col("small")
    return (g.withColumn("large_pct", F.round(F.col("large") / total * 100, 2))
             .withColumn("mid_pct", F.round(F.col("mid") / total * 100, 2))
             .withColumn("small_pct", F.round(F.col("small") / total * 100, 2))
             .drop("large", "mid", "small"))


def mart_turnover(spark, silver):
    """Month-over-month absolute weight change. Lower bound on true turnover."""
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    eq = silver.filter(F.col("asset_class") == "equity").select(
        "scheme_code", "disclosure_month", "company_id", "weight_pct")
    w = Window.partitionBy("scheme_code", "company_id").orderBy("disclosure_month")
    d = eq.withColumn("prev", F.lag("weight_pct").over(w))
    return (d.filter(F.col("prev").isNotNull())
             .withColumn("delta", F.abs(F.col("weight_pct") - F.col("prev")))
             .groupBy("scheme_code", "disclosure_month")
             .agg(F.round(F.sum("delta") * 0.5 * 12, 2).alias("inferred_turnover_pct")))


def run(spark, silver_path: str, schemes_path: str, universe_path: str,
        out_dir: str, salt_buckets: int = 0) -> dict:
    silver = spark.read.parquet(silver_path)
    schemes = spark.read.parquet(schemes_path)
    universe = spark.read.parquet(universe_path)

    marts = {
        "mart_overlap": mart_overlap(spark, silver, salt_buckets),
        "mart_crowding": mart_crowding(spark, silver, schemes, universe),
        "mart_style_drift": mart_style_drift(spark, silver, universe),
        "mart_turnover": mart_turnover(spark, silver),
    }
    counts = {}
    for name, df in marts.items():
        path = f"{out_dir}/{name}"
        df.write.mode("overwrite").parquet(path)
        counts[name] = spark.read.parquet(path).count()
        log.info("%-18s -> %-40s %d rows", name, path, counts[name])
    return counts


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--silver", default="data/warehouse/silver_holdings.parquet")
    p.add_argument("--schemes", default="data/warehouse/dim_scheme.parquet")
    p.add_argument("--universe", default="data/warehouse/dim_company.parquet")
    p.add_argument("--out", default="data/warehouse/gold")
    p.add_argument("--salt", type=int, default=0)
    a = p.parse_args()
    spark = build_session("fundxray-gold")
    import json
    print(json.dumps(run(spark, a.silver, a.schemes, a.universe, a.out, a.salt), indent=2))
    spark.stop()
