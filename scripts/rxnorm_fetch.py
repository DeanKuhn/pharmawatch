"""Fetch data from RxNorm API endpoint."""

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

BASE = "https://rxnav.nlm.nih.gov/REST"
target = Path("data/json/rxnav")
rxcui_dir = target / "rxcui"
rxcui_dir.mkdir(parents=True, exist_ok=True)

def fetch_rxnorm():
    con = duckdb.connect("dbt/pharmawatch_dev.duckdb", read_only=True)
    rows = con.sql(r"""
        with inner_pieces as (
            select distinct upper(trim(unnest(split(prod_ai, '\')))) as piece
            from main.stg_drug
            where prod_ai is not null
        )
        select * from inner_pieces
        where piece <> ''
    """).fetchall()

    with httpx.Client(timeout=30) as client: 
        version = client.get(f"{BASE}/version.json").json()
        version_file = target / "version.json"
        if not version_file.exists():
            version_json = json.dumps(version)
            version_file.write_text(version_json)
            log.info(f"Version: {version}")

        failed = 0
        succeeded = 0

        pieces = [r[0] for r in rows]
        for piece in pieces:
            if (succeeded + failed) % 500 == 0 and (succeeded + failed) != 0:
                log.info(f"Total names attempted: {succeeded + failed}")
                log.info(f"Succeeded: {succeeded}, Failed: {failed}")

            name = hashlib.sha1(piece.encode()).hexdigest()
            path = target / "rxcui" / f"{name}.json"
            if path.exists(): continue
            params = {"name": piece, "search": 2}
            resp = client.get(f"{BASE}/rxcui.json", params=params)

            if resp.status_code !=  200:
                log.warning(f"Failed to fetch {piece} with url {resp.url}")
                failed += 1
                time.sleep(0.06)
                continue

            record = {
                "piece": piece,
                "url": str(resp.url),
                "fetched_at": datetime.now(tz=UTC).isoformat(),
                "rxnorm_version": version["version"],
                "response": resp.json()
            }

            text = json.dumps(record, indent=2)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text)
            tmp.rename(path)
            log.info(f"Fetched data for {piece}, record at {path}")
            succeeded += 1
            time.sleep(0.06)

        log.info(f"Complete, total names attempted: {succeeded + failed}")
        log.info(f"Succeeded: {succeeded}, Failed: {failed}")


if __name__ == "__main__":
    fetch_rxnorm()
