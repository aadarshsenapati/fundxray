"""Cross-engine Parquet writing.

pandas/pyarrow default to nanosecond timestamps and will happily emit
all-null columns typed as `null`. Spark 3.5 can read neither:

    Illegal Parquet type: INT64 (TIMESTAMP(NANOS,false))

Since the same files are read by pandas, DuckDB and Spark, every write goes
through here so the physical types stay in the intersection all three support.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .utils.logging import get_logger

log = get_logger(__name__)


def spark_safe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        s = out[col]
        # Nanosecond -> microsecond timestamps
        if pd.api.types.is_datetime64_any_dtype(s):
            out[col] = s.astype("datetime64[us]")
        # All-null object columns arrive as pyarrow `null` type, which Spark
        # cannot represent. Give them a concrete type.
        elif s.isna().all():
            out[col] = s.astype("float64") if col.endswith(
                ("_value", "_pct", "quantity", "confidence")) else s.astype("string")
    return out


def write_parquet(df: pd.DataFrame, path: str | Path, **kw) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = spark_safe(df)
    table = pa.Table.from_pandas(safe, preserve_index=False)
    pq.write_table(table, path, coerce_timestamps="us",
                   allow_truncated_timestamps=True, **kw)
    return path


def assert_spark_readable(path: str | Path) -> None:
    """Cheap guard usable in tests and CI without starting a Spark session."""
    schema = pq.read_schema(Path(path))
    for field in schema:
        t = str(field.type)
        if "timestamp[ns" in t:
            raise TypeError(f"{path}:{field.name} is nanosecond timestamp; Spark cannot read it")
        if t == "null":
            raise TypeError(f"{path}:{field.name} has null type; Spark cannot read it")
