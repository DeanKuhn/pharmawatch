"""Dedup + schema-canonicalize FAERS Parquet, then sync it to R2 (decision 0005 --
report-level data lives as Parquet on R2, queried by DuckDB, never a database).

sync_quarters_to_r2 holds every quarter's DataFrame in memory at once (dedup
needs all quarters' DEMO simultaneously) -- a single full-archive run, not a
repeatable incremental sync. See docs/personal/r2_duckdb_motherduck_plan.md.
"""

import argparse
import logging
import polars as pl # type:ignore
from pathlib import Path

from faers.schema import apply_schema
from faers.dedup import keep_primaryids, apply_dedup, configure_logging
from faers.manifest import mark_stage, has_stage
from faers.r2 import R2Config, load_r2_config, raw_key, canonical_key, upload_parquet, download_parquet

logger = logging.getLogger(__name__)

FAERS_TABLES = ["demo", "drug", "indi", "outc", "reac", "rpsr", "ther"]

BIGINT_COLS: dict[str, list[str]] = {t: ["primaryid", "caseid"] for t in FAERS_TABLES}
INT_COLS: dict[str, list[str]] = {
    "demo": ["caseversion"],
    "drug": ["drug_seq"],
    "indi": ["indi_drug_seq"],
    "ther": ["dsg_drug_seq"],
}
NUMERIC_COLS: dict[str, list[str]] = {"demo": ["age", "wt"]}
DATE_COLS: dict[str, list[str]] = {"demo": ["mfr_dt", "init_fda_dt", "fda_dt"]}


def cast_canonical_types(df: pl.DataFrame, table: str) -> pl.DataFrame:
    """Cast df's Utf8 columns to typed columns for the canonical R2 Parquet."""
    casts = [
        pl.col(c).cast(pl.Int64, strict=False)
        for c in BIGINT_COLS.get(table, []) if c in df.columns
    ] + [
        pl.col(c).cast(pl.Int32, strict=False)
        for c in INT_COLS.get(table, []) if c in df.columns
    ] + [
        pl.col(c).cast(pl.Float64, strict=False)
        for c in NUMERIC_COLS.get(table, []) if c in df.columns
    ] + [
        pl.col(c).str.strptime(pl.Date, "%Y%m%d", strict=False)
        for c in DATE_COLS.get(table, []) if c in df.columns
    ]
    if not casts:
        return df

    original = df
    df = df.with_columns(casts)

    for c in DATE_COLS.get(table, []):
        if c not in df.columns:
            continue
        bad = df.filter(original[c].is_not_null() & df[c].is_null())
        if bad.height > 0:
            logger.warning(
                f"{table}.{c}: {bad.height} row(s) didn't parse as an 8-digit "
                f"date and were nulled (primaryid(s): {bad['primaryid'].to_list()[:10]})"
            )
    return df


def load_table_across_quarters(
    table: str, quarters: list[str], parquet_dir: Path, config: R2Config
) -> pl.DataFrame:
    """Read + apply_schema `table` for every quarter, concat into one DataFrame.

    Every quarter is needed on every run (dedup is global, not incremental --
    see module docstring), but local disk is scratch space per CLAUDE.md: raw
    Parquet for older quarters may have been deleted after a previous sync
    already pushed it to R2's raw/ zone. When the local file is missing, pull
    it back from R2 instead of raising, so deleting local files never forces
    a re-download-and-reparse of the original FAERS zip.
    """
    df_list = []
    for q in quarters:
        path = parquet_dir / q / f"{table}.parquet"
        if path.exists():
            df = pl.read_parquet(path)
        else:
            logger.info(f"{q}/{table}.parquet not found locally -- fetching from R2 raw zone.")
            df = download_parquet(raw_key(table, q), config)
        df = apply_schema(df, table, q)
        df_list.append(df)
    return pl.concat(df_list, how="diagonal")


def sync_quarters_to_r2(
    quarters: list[str],
    parquet_dir: Path,
    config: R2Config,
) -> None:
    """Dedup across all quarters, upload the canonical Parquet (overwritten each
    run, no versioning), then upload each quarter's raw Parquet if not
    already marked uploaded_raw.
    """
    tables = {t: load_table_across_quarters(t, quarters, parquet_dir, config) for t in FAERS_TABLES}
    keep = keep_primaryids(tables)
    deduped = apply_dedup(tables, keep)

    for table, df in deduped.items():
        df = cast_canonical_types(df, table)
        logger.info(f"Uploading canonical {table} ({df.height} rows)...")
        upload_parquet(df, canonical_key(table), config)

    for q in quarters:
        for table in FAERS_TABLES:
            if has_stage(q, "uploaded_raw", table):
                continue
            path = parquet_dir / q / f"{table}.parquet"
            if path.exists():
                upload_parquet(pl.read_parquet(path), raw_key(table, q), config)
                logger.info(f"Uploaded raw {q}/{table}")
            else:
                # Local file already gone but "uploaded_raw" was never marked --
                # the only way both are true is a prior run's upload_parquet
                # succeeding and then the process dying before mark_stage ran.
                # The object is already on R2; download_parquet here confirms
                # that (raises if it's somehow not) instead of re-uploading.
                download_parquet(raw_key(table, q), config)
                logger.info(
                    f"{q}/{table} already on R2 (local file gone) -- marking uploaded_raw without re-upload"
                )
            mark_stage(q, "uploaded_raw", table)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("quarters", nargs="+", help="e.g. 2019q1 2014q2")
    parser.add_argument("--parquet-dir", type=Path, default=Path("data/parquet"))
    args = parser.parse_args()

    config = load_r2_config()

    try:
        sync_quarters_to_r2(args.quarters, args.parquet_dir, config)
    except Exception:
        logger.exception("Sync failed")
        raise


if __name__ == "__main__":
    main()