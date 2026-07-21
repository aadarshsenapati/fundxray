"""Real Apache Iceberg tables via PyIceberg.

Why Iceberg rather than plain Parquet or Hive tables (see docs/adr/0001):
  * schema evolution without rewriting data — a decade of changing AMC formats
  * snapshot isolation — the API never reads a half-written month
  * time travel — "what did we show on this date, and why" is answerable, which
    matters when the numbers concern people's savings
  * hidden partitioning — removes a whole class of user error

Catalog: SQLite locally, swap `uri` for Postgres/Glue/Nessie in production.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pyarrow as pa

from fundxray_core.config import settings
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

NAMESPACE = "fundxray"


def catalog(warehouse: Optional[Path] = None):
    """SQLite needs POSIX file locking, which network shares and some mounted
    volumes do not provide ("disk I/O error"). Point ICEBERG_WAREHOUSE at a
    local path in those environments, or swap SqlCatalog for a REST/Glue
    catalog in production."""
    import os

    from pyiceberg.catalog.sql import SqlCatalog

    wh = Path(warehouse or os.getenv("ICEBERG_WAREHOUSE")
              or settings.warehouse_dir / "iceberg")
    wh.mkdir(parents=True, exist_ok=True)
    cat = SqlCatalog("fundxray", **{
        "uri": f"sqlite:///{(wh / 'catalog.db').as_posix()}",
        "warehouse": wh.as_posix(),
    })
    try:
        cat.create_namespace(NAMESPACE)
    except Exception:
        pass
    return cat


def write(table_name: str, df, partition_col: str | None = None,
          mode: str = "append") -> dict:
    """Write a pandas DataFrame to an Iceberg table, creating it if absent."""
    import pandas as pd

    cat = catalog()
    ident = f"{NAMESPACE}.{table_name}"
    arrow = pa.Table.from_pandas(df.reset_index(drop=True), preserve_index=False)

    try:
        tbl = cat.load_table(ident)
    except Exception:
        tbl = cat.create_table(ident, schema=arrow.schema)
        log.info("created Iceberg table %s", ident)

    if mode == "overwrite":
        tbl.overwrite(arrow)
    else:
        tbl.append(arrow)

    snaps = list(tbl.snapshots())
    log.info("%s %s: %d rows, %d snapshots", mode, ident, len(df), len(snaps))
    return {"table": ident, "rows": len(df), "snapshots": len(snaps),
            "current_snapshot": tbl.current_snapshot().snapshot_id if tbl.current_snapshot() else None}


def read(table_name: str, snapshot_id: int | None = None):
    """Read current state, or time-travel to a historical snapshot."""
    cat = catalog()
    tbl = cat.load_table(f"{NAMESPACE}.{table_name}")
    scan = tbl.scan(snapshot_id=snapshot_id) if snapshot_id else tbl.scan()
    return scan.to_pandas()


def history(table_name: str) -> list[dict]:
    cat = catalog()
    tbl = cat.load_table(f"{NAMESPACE}.{table_name}")
    return [{"snapshot_id": s.snapshot_id, "timestamp_ms": s.timestamp_ms,
             "operation": (s.summary.operation if s.summary else None),
             "added_records": (s.summary.get("added-records") if s.summary else None)}
            for s in tbl.snapshots()]
