"""Bronze -> Silver on Spark.

Distributed version of the pandas path in pipelines/ingestion/amfi/disclosures.py.
Same semantics, built for the full historical corpus rather than one month:
  * broadcast join against the reference universe (small dimension, big fact)
  * asset-class confirmation after resolution
  * deduplication on the natural key
  * provenance preserved end to end
"""
from __future__ import annotations

import argparse

from fundxray_core.utils.logging import get_logger
from pipelines.spark.session import build_session

log = get_logger(__name__)

NATURAL_KEY = ["scheme_code", "disclosure_month", "company_id", "instrument_name_raw"]


def build(spark, bronze_path: str, universe_path: str, out_path: str):
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    bronze = spark.read.parquet(bronze_path)
    uni = spark.read.parquet(universe_path).select(
        F.col("isin").alias("u_isin"), F.col("company_name").alias("u_name"))

    # ISIN-first resolution. The reference universe is tiny; broadcast it rather
    # than shuffling the fact table.
    resolved = (bronze
                .join(F.broadcast(uni), bronze.isin == uni.u_isin, "left")
                .withColumn("company_id", F.col("u_isin"))
                .withColumn("resolution_method",
                            F.when(F.col("u_isin").isNotNull(), F.lit("isin"))
                             .otherwise(F.lit("unresolved")))
                .drop("u_isin", "u_name"))

    # Equity candidates that failed to resolve are demoted so they can never
    # inflate look-through exposure.
    confirmed = resolved.withColumn(
        "asset_class",
        F.when((F.col("asset_class") == "equity") & F.col("company_id").isNull(),
               F.lit("other")).otherwise(F.col("asset_class")))

    # Keep the most recently parsed row per natural key.
    w = Window.partitionBy(*NATURAL_KEY).orderBy(F.col("parsed_at").desc())
    deduped = (confirmed.withColumn("_rn", F.row_number().over(w))
                        .filter(F.col("_rn") == 1).drop("_rn"))

    (deduped.repartition("disclosure_month")
            .write.mode("overwrite").partitionBy("disclosure_month")
            .parquet(out_path))
    return deduped


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bronze", required=True)
    p.add_argument("--universe", default="data/warehouse/dim_company.parquet")
    p.add_argument("--out", default="data/warehouse/silver_spark")
    a = p.parse_args()

    spark = build_session("fundxray-silver")
    df = build(spark, a.bronze, a.universe, a.out)
    n = df.count()
    eq = df.filter("asset_class = 'equity'").count()
    res = df.filter("asset_class = 'equity' AND company_id IS NOT NULL").count()
    log.info("silver rows=%d equity=%d resolved=%d (%.1f%%)",
             n, eq, res, (res / eq * 100) if eq else 0)
    spark.stop()
