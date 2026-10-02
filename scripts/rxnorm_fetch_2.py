"""Fetch data from RxNorm API endpoint for IN, TTY, PIN, BN, and relate to IN."""

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
properties_dir = target / "properties"
related_dir = target / "related"
properties_dir.mkdir(parents=True, exist_ok=True)
related_dir.mkdir(parents=True, exist_ok=True)

def fetch_rxnorm_pass_2():
    con = duckdb.connect()
    rows = con.sql(r"""
        select distinct unnest(response.idGroup.rxnormId) as rxcui
        from read_json('data/json/rxnav/rxcui/*.json')
    """).fetchall()
    ids = [r[0] for r in rows]
    
    print(f"Total number of distinct ids: {len(rows)}")
    print("Looks right? [y/n]")
    check = input(">>>").strip().lower()
    if check != 'y':
        return

    with httpx.Client(timeout=30) as client: 
        version_file = target / "version.json"
        if not version_file.exists():
            log.error("Previous version file for pass 1 does not exist, cannot verify version")
            return

        with open(version_file, "r") as f:
            loaded = json.load(f)
        loaded_version = loaded["version"]

        live = client.get(f"{BASE}/version.json").json()
        live_version = live["version"]
        if loaded_version != live_version:
            log.error(f"Loaded version {loaded_version} doesn't match live version {live_version}")
            return

        failed_prop = 0
        failed_rel = 0
        succeeded_prop = 0
        succeeded_rel = 0

        for rxcui in ids:
            if ((succeeded_prop + failed_prop) % 500 == 0) and (succeeded_prop + failed_prop != 0):
                log.info(f"Succeeded / Failed Properties: {succeeded_prop} / {failed_prop}")
                log.info(f"Succeeded / Failed Related: {succeeded_rel} / {failed_rel}")

            prop_path = properties_dir / f"{rxcui}.json"
            rel_path = related_dir / f"{rxcui}.json"

            if not prop_path.exists():
                properties = client.get(f"{BASE}/rxcui/{rxcui}/properties.json")
                if properties.status_code != 200:
                    log.warning(f"Failed to fetch properties for {rxcui} with url {properties.url}")
                    failed_prop += 1
                    time.sleep(0.06)
                else:
                    prop_record = {
                        "rxcui": rxcui,
                        "url": str(properties.url),
                        "fetched_at": datetime.now(tz=UTC).isoformat(),
                        "rxnorm_version": live_version,
                        "response": properties.json()
                    }
                    prop_text = json.dumps(prop_record, indent=2)
                    tmp = prop_path.with_suffix(".tmp")
                    tmp.write_text(prop_text)
                    tmp.rename(prop_path)
                    log.info(f"Fetched data for properties of {rxcui}")
                    succeeded_prop += 1
                    time.sleep(0.06)
                
            if not rel_path.exists():
                params = {"tty": "IN"}
                related = client.get(f"{BASE}/rxcui/{rxcui}/related.json", params=params)
                if related.status_code != 200:
                    log.warning(f"Failed to fetch related for {rxcui} with url {related.url}")
                    failed_rel += 1
                    time.sleep(0.06)
                else:
                    rel_record = {
                        "rxcui": rxcui,
                        "url": str(related.url),
                        "fetched_at": datetime.now(tz=UTC).isoformat(),
                        "rxnorm_version": live_version,
                        "response": related.json()
                    }
                    rel_text = json.dumps(rel_record, indent=2)
                    tmp = rel_path.with_suffix(".tmp")
                    tmp.write_text(rel_text)
                    tmp.rename(rel_path)
                    log.info(f"Fetched data for related of {rxcui}")
                    succeeded_rel += 1
                    time.sleep(0.06)

        log.info(f"Succeeded / Failed Properties: {succeeded_prop} / {failed_prop}")
        log.info(f"Succeeded / Failed Related: {succeeded_rel} / {failed_rel}")


if __name__ == "__main__":
    fetch_rxnorm_pass_2()
