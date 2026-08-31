"""R2 credential handling and low-level Parquet I/O."""

import os
from dataclasses import dataclass

import duckdb
import polars as pl
from dotenv import load_dotenv


@dataclass(frozen=True)
class R2Config:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str


def load_r2_config() -> R2Config:
    load_dotenv()
    r2_endpoint_url = os.environ["R2_ENDPOINT_URL"]
    r2_access_key_id = os.environ["R2_ACCESS_KEY_ID"]
    r2_secret_access_key = os.environ["R2_SECRET_ACCESS_KEY"]
    r2_bucket_name = os.environ["R2_BUCKET_NAME"]
    return R2Config(
        endpoint_url=r2_endpoint_url,
        access_key_id=r2_access_key_id,
        secret_access_key=r2_secret_access_key,
        bucket=r2_bucket_name,
    )


def storage_options(config: R2Config) -> dict:
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


def configure_duckdb_r2(con: duckdb.DuckDBPyConnection, config: R2Config) -> None:
    con.install_extension("httpfs")
    con.load_extension("httpfs")
    con.execute(f"SET s3_endpoint = '{config.endpoint_url.removeprefix('https://')}'")
    con.execute(f"SET s3_access_key_id = '{config.access_key_id}'")
    con.execute(f"SET s3_secret_access_key = '{config.secret_access_key}'")
    con.execute("SET s3_region = 'auto'")
    con.execute("SET s3_url_style = 'path'")


def upload_parquet(rel: duckdb.DuckDBPyRelation, key: str, config: R2Config) -> None:
    rel.write_parquet(f"s3://{config.bucket}/{key}")


def download_parquet(key: str, config: R2Config) -> pl.DataFrame:
    return pl.read_parquet(
        f"s3://{config.bucket}/{key}", storage_options=storage_options(config)
    )

