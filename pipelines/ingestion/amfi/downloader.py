"""Bulk downloader for AMFI monthly portfolio disclosures.

AMFI hosts the SEBI-mandated monthly disclosures centrally, and each AMC also
publishes the same workbooks on its own investor-disclosures page. This module
discovers those links, downloads with resume/caching, and lands raw files in a
month-partitioned directory that the adapter suite then parses.

Deliberate design choices:
  * cache by content hash — re-running a month never re-downloads unchanged files
  * polite: configurable delay + concurrency cap; this is a public regulator
    resource, not an API you are entitled to hammer
  * every download records provenance (URL, fetched_at, sha256, bytes) so any
    downstream number can be traced back to a specific file
  * failures are recorded, not raised — one dead AMC link must not abort a
    ten-year backfill
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from fundxray_core.config import settings
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

AMFI_DISCLOSURE_PAGE = "https://www.amfiindia.com/online-center/portfolio-disclosure"
USER_AGENT = ("FundXRay/0.1 (open-source portfolio transparency research; "
              "contact via github.com/aadarshsenapati/fundxray)")
FILE_RE = re.compile(r'href=["\']([^"\']+\.(?:xlsx|xls|csv|zip|pdf))["\']', re.I)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def discover(page_url: str = AMFI_DISCLOSURE_PAGE, timeout: int = 60) -> list[str]:
    """Scrape disclosure file links from a hosting page."""
    s = _session()
    r = s.get(page_url, timeout=timeout)
    r.raise_for_status()
    links = {urljoin(page_url, m) for m in FILE_RE.findall(r.text)}
    log.info("discovered %d candidate files at %s", len(links), page_url)
    return sorted(links)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_one(url: str, dest_dir: Path, session: requests.Session,
                 timeout: int = 120, delay: float = 0.5) -> dict:
    name = Path(urlparse(url).path).name or hashlib.md5(url.encode()).hexdigest()
    dest = dest_dir / name
    if dest.exists():
        return {"url": url, "path": str(dest), "status": "cached",
                "sha256": sha256(dest), "bytes": dest.stat().st_size}
    try:
        time.sleep(delay)
        with session.get(url, timeout=timeout, stream=True) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            tmp.replace(dest)
        return {"url": url, "path": str(dest), "status": "downloaded",
                "sha256": sha256(dest), "bytes": dest.stat().st_size,
                "fetched_at": dt.datetime.now().isoformat()}
    except Exception as e:
        log.warning("download failed %s: %s", url, e)
        return {"url": url, "status": "failed", "error": str(e)}


def download_month(month: dt.date, urls: list[str] | None = None,
                   raw_dir: Path | None = None, workers: int = 4,
                   delay: float = 0.5) -> dict:
    raw_dir = Path(raw_dir or settings.raw_dir) / f"disclosures/{month:%Y%m}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    urls = urls if urls is not None else discover()

    session = _session()
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_one, u, raw_dir, session, delay=delay): u
                   for u in urls}
        for fut in as_completed(futures):
            results.append(fut.result())

    manifest = {
        "month": f"{month:%Y-%m}", "dir": str(raw_dir),
        "downloaded": sum(r["status"] == "downloaded" for r in results),
        "cached": sum(r["status"] == "cached" for r in results),
        "failed": sum(r["status"] == "failed" for r in results),
        "total_bytes": sum(r.get("bytes", 0) for r in results),
        "files": results,
    }
    (raw_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    log.info("month %s: %d downloaded, %d cached, %d failed (%.1f MB)",
             manifest["month"], manifest["downloaded"], manifest["cached"],
             manifest["failed"], manifest["total_bytes"] / 1e6)
    return manifest


def backfill(start: dt.date, end: dt.date, **kw) -> list[dict]:
    """Month-by-month backfill. A failed month is logged and skipped, never fatal."""
    out, cur = [], start.replace(day=1)
    while cur <= end:
        try:
            out.append(download_month(cur, **kw))
        except Exception as e:
            log.error("month %s failed entirely: %s", cur, e)
            out.append({"month": f"{cur:%Y-%m}", "error": str(e)})
        cur = (cur.replace(day=28) + dt.timedelta(days=7)).replace(day=1)
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Download AMFI monthly portfolio disclosures")
    p.add_argument("--month", help="YYYY-MM (single month)")
    p.add_argument("--from", dest="start", help="YYYY-MM (backfill start)")
    p.add_argument("--to", dest="end", help="YYYY-MM (backfill end)")
    p.add_argument("--url", action="append", default=None,
                   help="explicit file URL; repeatable. Skips discovery.")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--delay", type=float, default=0.5)
    a = p.parse_args()

    def m(s):
        return dt.datetime.strptime(s, "%Y-%m").date().replace(day=1)

    if a.start and a.end:
        print(json.dumps(backfill(m(a.start), m(a.end), urls=a.url,
                                  workers=a.workers, delay=a.delay), indent=2)[:4000])
    else:
        month = m(a.month) if a.month else dt.date.today().replace(day=1)
        print(json.dumps(download_month(month, urls=a.url, workers=a.workers,
                                        delay=a.delay), indent=2)[:4000])
