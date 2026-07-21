"""Spark session factory.

Iceberg note: on a machine with Maven access, pass `iceberg=True` to pull the
runtime jar and register the catalog. In an air-gapped environment (like CI
here) that download is unavailable, so the Iceberg tables are written with
PyIceberg instead — see pipelines/lake/iceberg_catalog.py. Both produce real
Iceberg metadata; only the writer differs.
"""
from __future__ import annotations

import os
import socket

ICEBERG_PKG = "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0"


def build_session(app: str = "fundxray", cores: str | None = None,
                  shuffle_partitions: int = 32, aqe: bool = True,
                  iceberg: bool = False, warehouse: str = "data/warehouse/iceberg"):
    from pyspark.sql import SparkSession

    # Spark resolves the local hostname at startup and dies if it cannot.
    # Containers frequently have a hostname with no DNS entry, so pin the
    # driver to loopback rather than relying on name resolution.
    try:
        socket.gethostbyname(socket.gethostname())
    except OSError:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")

    if not os.environ.get("JAVA_HOME"):
        for candidate in ("/usr/lib/jvm/java-11-openjdk-amd64",
                          "/usr/lib/jvm/java-17-openjdk-amd64",
                          "/usr/lib/jvm/default-java"):
            if os.path.isdir(candidate):
                os.environ["JAVA_HOME"] = candidate
                break

    b = (SparkSession.builder
         .appName(app)
         .master(cores or os.getenv("SPARK_MASTER", "local[*]"))
         .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
         .config("spark.sql.adaptive.enabled", str(aqe).lower())
         .config("spark.sql.adaptive.skewJoin.enabled", str(aqe).lower())
         .config("spark.driver.memory", os.getenv("SPARK_DRIVER_MEMORY", "2g"))
         .config("spark.ui.showConsoleProgress", "false")
         .config("spark.sql.session.timeZone", "Asia/Kolkata")
         .config("spark.driver.host", os.getenv("SPARK_DRIVER_HOST", "127.0.0.1"))
         .config("spark.driver.bindAddress", "127.0.0.1"))

    if iceberg:
        b = (b.config("spark.jars.packages", ICEBERG_PKG)
              .config("spark.sql.extensions",
                      "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
              .config("spark.sql.catalog.fundxray",
                      "org.apache.iceberg.spark.SparkCatalog")
              .config("spark.sql.catalog.fundxray.type", "hadoop")
              .config("spark.sql.catalog.fundxray.warehouse", warehouse))

    spark = b.getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark
