"""R2 credential handling and low-level Parquet I/O.

One place for this so the sync step (load.py, Phase 2), the DuckDB connection
setup (warehouse.py, Phase 3), and tests all share the same config/key shape
instead of each re-deriving env var names or key layouts.

Two R2 prefixes, not one:
- faers/raw/{quarter}/{table}.parquet -- parse.py's raw per-era output,
  uploaded once per quarter, append-only. This is the actual "immutable raw
  zone" CLAUDE.md's architecture describes.
- faers/canonical/{table}.parquet -- dedup.py/schema.py's output, fully
  recomputed and overwritten each time load.py's sync runs. What DuckDB/dbt
  query.
"""

from dataclasses import dataclass

import os
import polars as pl  # type:ignore
from dotenv import load_dotenv  # type:ignore


@dataclass(frozen=True)
class R2Config:
    endpoint_url: str  # https://<account_id>.r2.cloudflarestorage.com
    access_key_id: str
    secret_access_key: str
    bucket: str


def load_r2_config() -> R2Config:
    """Read R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
    R2_BUCKET_NAME via load_dotenv(), same pattern as load.py's
    NEON_DATABASE_URL. Raise a clear error (KeyError message or explicit
    ValueError) naming whichever var is missing, rather than letting a bare
    KeyError surface deep inside a polars/duckdb call.
    """
    load_dotenv()
    r2_endpoint_url = os.environ["R2_ENDPOINT_URL"]
    r2_access_key_id = os.environ["R2_ACCESS_KEY_ID"]
    r2_secret_access_key = os.environ["R2_SECRET_ACCESS_KEY"]
    r2_bucket_name = os.environ["R2_BUCKET_NAME"]
    return R2Config(
        endpoint_url=r2_endpoint_url,
        access_key_id=r2_access_key_id,
        secret_access_key=r2_secret_access_key,
        bucket=r2_bucket_name)


def storage_options(config: R2Config) -> dict:
    """Translate R2Config into what polars' write_parquet/read_parquet expect
    for an S3-compatible endpoint (the object_store crate underneath polars):
    aws_endpoint_url, aws_access_key_id, aws_secret_access_key, and
    aws_region='auto' (R2 has no real regions, but the S3 API requires the
    field to be set to something).
    """
    return {
        "aws_endpoint_url": config.endpoint_url,
        "aws_access_key_id": config.access_key_id,
        "aws_secret_access_key": config.secret_access_key,
        "aws_region": "auto",
    }


def raw_key(table: str, quarter: str) -> str:
    return f"faers/raw/{quarter}/{table}.parquet"

def canonical_key(table: str) -> str:
    return f"faers/canonical/{table}.parquet"


def upload_parquet(df: pl.DataFrame, key: str, config: R2Config) -> None:
    df.write_parquet(f"s3://{config.bucket}/{key}", storage_options=storage_options(config))


def download_parquet(key: str, config: R2Config) -> pl.DataFrame:
    return pl.read_parquet(f"s3://{config.bucket}/{key}", storage_options=storage_options(config))
