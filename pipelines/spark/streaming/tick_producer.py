"""Tick producer: SmartAPI WebSocket -> Kafka.

Two modes:
  live    — subscribe to the SmartAPI WebSocket for the ~500 stocks that
            dominate mutual fund holdings and publish each tick
  replay  — replay historical candles as a synthetic tick stream, deliberately
            injecting late, duplicate and malformed events so the consumer's
            correctness guarantees are actually exercised

Replay mode is what makes the recovery proof reproducible in CI: it needs no
market hours, no broker session, and no credentials.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import time
from pathlib import Path

from fundxray_core.config import settings
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

TOPIC = "marketdata.ticks"


def _kafka_producer(bootstrap: str):
    from kafka import KafkaProducer
    return KafkaProducer(bootstrap_servers=bootstrap,
                         value_serializer=lambda v: json.dumps(v).encode(),
                         key_serializer=lambda k: str(k).encode(),
                         acks="all", retries=5, enable_idempotence=True)


def make_tick(isin: str, token: str, price: float, seq: int,
              ts: dt.datetime | None = None) -> dict:
    ts = ts or dt.datetime.now()
    return {"event_id": f"{token}-{seq}", "isin": isin, "token": token,
            "ltp": round(price, 2), "seq": seq,
            "event_time": ts.isoformat(timespec="milliseconds")}


def replay(out_dir: Path, n_symbols: int = 20, ticks_per_symbol: int = 50,
           late_rate: float = 0.05, dupe_rate: float = 0.05,
           malformed_rate: float = 0.02, batch_size: int = 100,
           seed: int = 11) -> dict:
    """Write newline-delimited JSON batches — a file source stands in for Kafka
    so the same Structured Streaming semantics can be proven without a broker."""
    import pandas as pd

    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    uni = pd.read_parquet(settings.warehouse_dir / "dim_company.parquet").head(n_symbols)
    now = dt.datetime.now()

    events, expected = [], {}
    for _, r in uni.iterrows():
        price = 100.0
        for seq in range(ticks_per_symbol):
            price *= (1 + rng.gauss(0, 0.004))
            ts = now + dt.timedelta(milliseconds=seq * 250)
            tick = make_tick(r["isin"], str(r["smartapi_token"]), price, seq, ts)
            events.append(tick)
            expected[tick["event_id"]] = tick["ltp"]

            if rng.random() < dupe_rate:                    # exact duplicate
                events.append(dict(tick))
            if rng.random() < late_rate:                    # out-of-order arrival
                late = dict(tick)
                late["event_time"] = (ts - dt.timedelta(seconds=30)).isoformat(
                    timespec="milliseconds")
                late["event_id"] = tick["event_id"] + "-late"
                events.append(late)
                expected[late["event_id"]] = late["ltp"]
            if rng.random() < malformed_rate:               # unparseable payload
                events.append({"event_id": None, "isin": r["isin"], "ltp": "NOT_A_NUMBER"})

    rng.shuffle(events)
    files = 0
    for i in range(0, len(events), batch_size):
        batch = events[i:i + batch_size]
        (out_dir / f"ticks_{files:05d}.json").write_text(
            "\n".join(json.dumps(e) for e in batch))
        files += 1

    stats = {"events_written": len(events), "files": files,
             "distinct_valid_events": len(expected), "symbols": len(uni)}
    log.info("replay: %(events_written)d events in %(files)d files, "
             "%(distinct_valid_events)d distinct valid", stats)
    (out_dir.parent / "expected.json").write_text(json.dumps(expected))
    return stats


def live(bootstrap: str, tokens: list[str]) -> None:  # pragma: no cover - needs a session
    """SmartAPI WebSocket -> Kafka. Requires credentials and market hours."""
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2

    from pipelines.ingestion.angelone.client import get_client

    client = get_client()
    client.connect()
    producer = _kafka_producer(bootstrap)
    seq = {"n": 0}

    def on_data(_ws, message):
        seq["n"] += 1
        tick = {"event_id": f"{message.get('token')}-{seq['n']}",
                "token": message.get("token"),
                "ltp": message.get("last_traded_price", 0) / 100.0,
                "seq": seq["n"],
                "event_time": dt.datetime.now().isoformat(timespec="milliseconds")}
        producer.send(TOPIC, key=tick["token"], value=tick)

    sws = SmartWebSocketV2(client._sc.getfeedToken(), settings.smartapi_api_key,
                           settings.smartapi_client_id, client._sc.getfeedToken())
    sws.on_data = on_data
    sws.on_open = lambda ws: sws.subscribe("fundxray", 1,
                                           [{"exchangeType": 1, "tokens": tokens}])
    sws.connect()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["replay", "live"], default="replay")
    p.add_argument("--out", default="/tmp/fx_stream/input")
    p.add_argument("--bootstrap", default="localhost:9092")
    a = p.parse_args()
    if a.mode == "replay":
        print(json.dumps(replay(Path(a.out)), indent=2))
    else:
        live(a.bootstrap, [])
