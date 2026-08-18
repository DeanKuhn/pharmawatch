"""Dedup + schema-canonicalize FAERS Parquet, then sync to R2 (decision 0005).
Full-archive run, not incremental: dedup needs all quarters in memory at once.

Flow. Both entry points run the same spine; they differ only in where the
canonical Parquet lands and whether R2 is touched at all.

    main
     |-- dry_run_to_dir ------.  local canonical_dir, no R2
     `-- sync_quarters_to_r2 -'  staging_dir then upload
           1. build_keep_cache      one connection, all 7 tables unioned,
                                    retractions dropped -> _keep.parquet
           2. per table, fresh con  build_canonical_table
                                      -> write_canonical_local [-> upload]
           3. deleted_caseids       shipped as its own canonical artifact
           4. sync_raw_zone         (sync only) per-quarter raw upload
                                    + manifest stage marks

Step 2's per-table relation is itself a small pipeline:
load_table_across_quarters -> (demo only: exclude_deleted) -> dedup_table
-> cast_canonical_types.

The connection lifecycle in steps 1-3 is not stylistic -- see `_connect`.
"""

import argparse
import itertools
import logging
from pathlib import Path

import duckdb  # type:ignore
import pyarrow.parquet as pq  # type:ignore

from faers.dedup import (
    configure_logging,
    dedup_table,
    keep_relation,
    log_cross_caseid_conflicts,
)
from faers.deleted import DELETED_PARQUET_NAME, deleted_parquet_path
from faers.manifest import has_stage, mark_stage
from faers.r2 import (
    R2Config,
    canonical_key,
    configure_duckdb_r2,
    download_parquet,
    load_r2_config,
    raw_key,
    upload_parquet,
)
from faers.schema import canonical_select_sql

logger = logging.getLogger(__name__)

_view_names = (f"__load_view_{i}" for i in itertools.count())


def _unique_alias() -> str:
    return next(_view_names)


FAERS_TABLES = ["demo", "drug", "indi", "outc", "reac", "rpsr", "ther"]

BIGINT_COLS: dict[str, list[str]] = {
    t: ["primaryid", "caseid"] for t in FAERS_TABLES
}
INT_COLS: dict[str, list[str]] = {
    "demo": ["caseversion"],
    "drug": ["drug_seq"],
    "indi": ["indi_drug_seq"],
    "ther": ["dsg_drug_seq"],
}
NUMERIC_COLS: dict[str, list[str]] = {"demo": ["age", "wt"]}
DATE_COLS: dict[str, list[str]] = {"demo": ["mfr_dt", "init_fda_dt", "fda_dt"]}

_DRY_RUN_CONFIG = R2Config(
    endpoint_url="", access_key_id="", secret_access_key="", bucket="unused"
)


def main() -> None:
    """Dedup all quarters and sync canonical + raw Parquet to R2."""
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("quarters", nargs="+", help="e.g. 2019q1 2014q2")
    parser.add_argument(
        "--parquet-dir", type=Path, default=Path("data/parquet")
    )
    parser.add_argument(
        "--canonical-dir",
        type=Path,
        default=None,
        help=(
            "Dry run: write canonical Parquet to this local directory and "
            "touch R2 not at all. Runs the identical compute path, so it is "
            "a real rehearsal of the load rather than a smoke test."
        ),
    )
    args = parser.parse_args()

    try:
        if args.canonical_dir is not None:
            dry_run_to_dir(
                args.quarters, args.parquet_dir, args.canonical_dir
            )
        else:
            sync_quarters_to_r2(
                args.quarters, args.parquet_dir, load_r2_config()
            )
    except Exception:
        logger.exception("Load failed")
        raise


def _total_memory_kb() -> int | None:
    """MemTotal of the machine actually running, or None if unreadable."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


DUCKDB_MEMORY_FRACTION = 0.55
DUCKDB_MEMORY_FALLBACK_MB = 4096


def duckdb_memory_limit_mb(fraction: float = DUCKDB_MEMORY_FRACTION) -> int:
    """Size DuckDB's budget from real RAM rather than a hardcoded guess.

    A limit above MemTotal is worse than none: DuckDB only spills as it nears
    its budget, so an unreachable one never spills and the kernel OOM-kills
    instead (2026-07-31, hardcoded 9GB on a 7.67GB VM: spill dir empty, died
    at 6.9GB RSS). The remaining fraction covers Python's heap and the OS,
    which this pragma does not govern.
    """
    total_kb = _total_memory_kb()
    if total_kb is None:
        return DUCKDB_MEMORY_FALLBACK_MB
    return int(total_kb * fraction / 1024)


def _connect(spill_dir: Path = Path("data/duckdb_spill")) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection capped and pointed at spill, per decision 0006.

    The cap sits below the systemd scope's MemoryMax in
    scripts/run_load_isolated.sh, leaving headroom for Python's own
    allocation inside the same cgroup. Callers open one per table and close it
    before the next -- see `build_canonical_table` for why.
    """
    con = duckdb.connect()
    spill_dir.mkdir(parents=True, exist_ok=True)
    limit_mb = duckdb_memory_limit_mb()
    total_kb = _total_memory_kb()
    logger.info(
        f"DuckDB memory_limit={limit_mb}MB, "
        f"MemTotal={total_kb // 1024 if total_kb else 'unknown'}MB, "
        f"spill={spill_dir}"
    )
    con.execute(f"PRAGMA memory_limit='{limit_mb}MB'")
    con.execute(f"PRAGMA temp_directory='{spill_dir}'")
    return con


def write_canonical_local(
    rel: duckdb.DuckDBPyRelation, name: str, dest_dir: Path
) -> tuple[Path, int]:
    """Execute `rel` once, to a local Parquet file, and report its row count.

    The count is read from the Parquet footer, so it costs no extra scan.
    Returns the path so callers upload bytes rather than recomputing `rel`.
    """
    dest = dest_dir / f"{name}.parquet"
    rel.write_parquet(str(dest))
    rows = pq.ParquetFile(dest).metadata.num_rows
    logger.info(f"Wrote {dest} ({rows:,} rows)")
    return dest, rows


def upload_local_parquet(
    con: duckdb.DuckDBPyConnection, path: Path, key: str, config: R2Config
) -> None:
    """Stream an already-written local Parquet file up to R2.

    Not from the canonical relation directly: that stacks the S3 writer's
    multipart buffers on top of the union/dedup/sort, which blew the memory
    budget on `reac` (2026-07-31). From a finished file it is a bounded scan.
    """
    con.sql(f"SELECT * FROM read_parquet('{path}')").write_parquet(
        f"s3://{config.bucket}/{key}"
    )


KEEP_CACHE_NAME = "_keep.parquet"


def build_keep_cache(
    quarters: list[str],
    parquet_dir: Path,
    config: R2Config,
    staging_dir: Path,
) -> Path:
    """Materialize the surviving-primaryid keep list to local Parquet.

    On disk rather than held as a relation so each table can then be built on
    its own connection.
    """
    con = _connect()
    try:
        tables = {
            table: load_table_across_quarters(
                con, table, quarters, parquet_dir, config
            )
            for table in FAERS_TABLES
        }
        deleted = load_deleted_caseids(con, parquet_dir)
        log_deleted_impact(tables["demo"], deleted)
        tables["demo"] = exclude_deleted(tables["demo"], deleted)

        dest = staging_dir / KEEP_CACHE_NAME
        keep_relation(tables).write_parquet(str(dest))
        logger.info(f"Wrote keep list to {dest}")
        log_cross_caseid_conflicts(
            con.sql(f"SELECT * FROM read_parquet('{dest}')")
        )
        return dest
    finally:
        con.close()


def build_canonical_table(
    con: duckdb.DuckDBPyConnection,
    name: str,
    quarters: list[str],
    parquet_dir: Path,
    config: R2Config,
    keep_path: Path,
) -> duckdb.DuckDBPyRelation:
    """One table's canonical relation, on a connection the caller owns.

    A connection per table, not one shared across all seven: DuckDB's buffer
    pool does not fully release between materializations. Sharing one killed
    the 5th table (`reac`, "failed to pin block 6.1GiB/6.1GiB", 2026-07-31);
    it then ran in 54s on a fresh connection, despite being narrower than
    `drug` which had already passed. Accumulation, not a table that won't fit.
    """
    keep = con.sql(f"SELECT * FROM read_parquet('{keep_path}')")
    rel = load_table_across_quarters(con, name, quarters, parquet_dir, config)
    if name == "demo":
        rel = exclude_deleted(rel, load_deleted_caseids(con, parquet_dir))
    return cast_canonical_types(dedup_table(name, rel, keep), name)


def dry_run_to_dir(
    quarters: list[str], parquet_dir: Path, canonical_dir: Path
) -> None:
    """Run the full compute path, writing canonical Parquet locally.

    Rehearses the real thing including peak memory -- the failure mode that
    bites at full-archive scale -- with no upload and no R2 credentials.
    Check the output with `validate.py --canonical-dir`.
    """
    missing = [
        str(parquet_dir / q / f"{t}.parquet")
        for q in quarters
        for t in FAERS_TABLES
        if not (parquet_dir / q / f"{t}.parquet").exists()
    ]
    if missing:
        raise FileNotFoundError(
            "A dry run reads only local Parquet -- there is no R2 fallback "
            f"to cover a missing quarter. Missing: {missing[:5]}"
            + (f" (+{len(missing) - 5} more)" if len(missing) > 5 else "")
        )

    canonical_dir.mkdir(parents=True, exist_ok=True)
    keep_path = build_keep_cache(
        quarters, parquet_dir, _DRY_RUN_CONFIG, canonical_dir
    )

    for name in FAERS_TABLES:
        con = _connect()
        try:
            rel = build_canonical_table(
                con, name, quarters, parquet_dir, _DRY_RUN_CONFIG, keep_path
            )
            write_canonical_local(rel, name, canonical_dir)
        finally:
            con.close()

    con = _connect()
    try:
        write_canonical_local(
            load_deleted_caseids(con, parquet_dir),
            "deleted_caseids",
            canonical_dir,
        )
    finally:
        con.close()

    logger.info(
        f"Dry run complete -- nothing uploaded. Check it with: "
        f"uv run python -m faers.validate {' '.join(quarters)} "
        f"--canonical-dir {canonical_dir}"
    )


def sync_quarters_to_r2(
    quarters: list[str],
    parquet_dir: Path,
    config: R2Config,
    staging_dir: Path = Path("data/canonical"),
) -> None:
    """Dedup across all quarters, upload canonical + raw Parquet to R2."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    keep_path = build_keep_cache(quarters, parquet_dir, config, staging_dir)

    for name in FAERS_TABLES:
        con = _connect()
        try:
            configure_duckdb_r2(con, config)
            rel = build_canonical_table(
                con, name, quarters, parquet_dir, config, keep_path
            )
            path, _ = write_canonical_local(rel, name, staging_dir)
            logger.info(f"Uploading canonical {name}...")
            upload_local_parquet(con, path, canonical_key(name), config)
        finally:
            con.close()

    # Shipped as its own artifact, not just applied and forgotten, so "which
    # cases were withheld" stays answerable downstream.
    con = _connect()
    try:
        configure_duckdb_r2(con, config)
        path, _ = write_canonical_local(
            load_deleted_caseids(con, parquet_dir),
            "deleted_caseids",
            staging_dir,
        )
        logger.info("Uploading canonical deleted_caseids...")
        upload_local_parquet(
            con, path, canonical_key("deleted_caseids"), config
        )

        sync_raw_zone(con, quarters, parquet_dir, config)
    finally:
        con.close()


def sync_raw_zone(
    con: duckdb.DuckDBPyConnection,
    quarters: list[str],
    parquet_dir: Path,
    config: R2Config,
) -> None:
    """Mirror each quarter's verbatim Parquet into the R2 raw zone.

    Independent of dedup, and idempotent via the manifest so a resumed run
    skips what already shipped. One sequential scan per file, so it can share
    the caller's connection.
    """
    for q in quarters:
        for table in FAERS_TABLES:
            if has_stage(q, "uploaded_raw", table):
                continue
            path = parquet_dir / q / f"{table}.parquet"
            if path.exists():
                upload_parquet(
                    con.sql(f"SELECT * FROM read_parquet('{path}')"),
                    raw_key(table, q),
                    config,
                )
                logger.info(f"Uploaded raw {q}/{table}")
            else:
                # Purged locally after a previous upload. Confirm it is really
                # on R2 before marking the stage, so a genuine gap still fails
                # loudly instead of being recorded as done.
                download_parquet(raw_key(table, q), config)
                logger.info(
                    f"{q}/{table} already on R2 (local file gone), "
                    "marking uploaded_raw without re-upload"
                )
            mark_stage(q, "uploaded_raw", table)

        deleted_path = deleted_parquet_path(q, parquet_dir)
        if deleted_path.exists() and not has_stage(
            q, "uploaded_raw", "deleted"
        ):
            upload_parquet(
                con.sql(f"SELECT * FROM read_parquet('{deleted_path}')"),
                raw_key("deleted", q),
                config,
            )
            mark_stage(q, "uploaded_raw", "deleted")
            logger.info(f"Uploaded raw {q}/deleted")


def _resolve_quarter_source(
    table: str, quarter: str, parquet_dir: Path, config: R2Config
) -> str:
    """Local path if present, else the R2 raw-zone s3:// URI as fallback.

    Split out so tests can assert the routing without httpfs resolving the
    URI over the network.
    """
    path = parquet_dir / quarter / f"{table}.parquet"
    if path.exists():
        return str(path)
    logger.info(
        f"{quarter}/{table}.parquet not found locally, "
        "reading from R2 raw zone."
    )
    return f"s3://{config.bucket}/{raw_key(table, quarter)}"


def load_table_across_quarters(
    con: duckdb.DuckDBPyConnection,
    table: str,
    quarters: list[str],
    parquet_dir: Path,
    config: R2Config,
) -> duckdb.DuckDBPyRelation:
    """Union `table` across all quarters, columns canonicalized."""
    selects = [
        canonical_select_sql(
            con, table, q, _resolve_quarter_source(table, q, parquet_dir, config)
        )
        for q in quarters
    ]
    return con.sql(" UNION ALL BY NAME ".join(selects))


def load_deleted_caseids(
    con: duckdb.DuckDBPyConnection, parquet_dir: Path
) -> duckdb.DuckDBPyRelation:
    """Union every deleted-case list on disk into one distinct caseid column.

    Globs the whole parquet dir, not just the quarters being loaded: FDA's
    retractions are not scoped to the quarter publishing them (2019q1
    withdraws cases first reported in 2004), so the exclusion set is always
    every retraction known to date. The lists overlap without nesting, so
    DISTINCT is load-bearing.
    """
    files = sorted(
        str(p) for p in parquet_dir.glob(f"*/{DELETED_PARQUET_NAME}")
    )
    if not files:
        logger.warning(
            f"No {DELETED_PARQUET_NAME} files under {parquet_dir} -- no "
            "FDA-retracted cases will be excluded. Run "
            "scripts/backfill_deleted.py first."
        )
        return con.sql("SELECT CAST(NULL AS BIGINT) AS caseid WHERE FALSE")

    rel = con.sql(f"SELECT DISTINCT caseid FROM read_parquet({files!r})")
    logger.info(
        f"Loaded {rel.aggregate('count(*) AS c').fetchone()[0]} distinct "
        f"deleted caseid(s) from {len(files)} quarterly list(s)"
    )
    return rel


def log_deleted_impact(
    demo: duckdb.DuckDBPyRelation, deleted: duckdb.DuckDBPyRelation
) -> int:
    """Record how many DEMO rows the retraction list removes.

    One extra scan of the smallest table, so the exclusion shows up in the log
    instead of only as an unexplained row-count drop.
    """
    alias = _unique_alias()
    excluded = demo.query(
        alias,
        f"""
        SELECT count(*) AS c
        FROM {alias} d
        SEMI JOIN deleted x ON TRY_CAST(d.caseid AS BIGINT) = x.caseid
        """,
    ).fetchone()[0]
    logger.info(f"Excluding {excluded} DEMO row(s) for FDA-retracted cases")
    return excluded


def exclude_deleted(
    demo: duckdb.DuckDBPyRelation, deleted: duckdb.DuckDBPyRelation
) -> duckdb.DuckDBPyRelation:
    """Drop FDA-retracted cases from DEMO, before `keep_relation` runs.

    DEMO only, which both suffices and is forced: children are filtered by
    primaryid membership in the keep list, and pre-2013 children carry `ISR`
    alone with no caseid to join on.

    Order matters. Filtering after `keep_relation` would let a retracted
    highest version win its case and then vanish, dropping the whole case
    instead of falling back to the next version.

    TRY_CAST because caseid is still VARCHAR here -- `cast_canonical_types`
    runs later, per table.
    """
    alias = _unique_alias()
    return demo.query(
        alias,
        f"""
        SELECT d.*
        FROM {alias} d
        ANTI JOIN deleted x ON TRY_CAST(d.caseid AS BIGINT) = x.caseid
        """,
    )


def cast_canonical_types(
    rel: duckdb.DuckDBPyRelation, table: str
) -> duckdb.DuckDBPyRelation:
    """Cast canonical columns from strings to their proper types."""
    bigint_cols = set(BIGINT_COLS.get(table, []))
    int_cols = set(INT_COLS.get(table, []))
    numeric_cols = set(NUMERIC_COLS.get(table, []))
    date_cols = [c for c in DATE_COLS.get(table, []) if c in rel.columns]

    select_list = []
    for c in rel.columns:
        if c in bigint_cols:
            select_list.append(f'TRY_CAST("{c}" AS BIGINT) AS "{c}"')
        elif c in int_cols:
            select_list.append(f'TRY_CAST("{c}" AS INTEGER) AS "{c}"')
        elif c in numeric_cols:
            select_list.append(f'TRY_CAST("{c}" AS DOUBLE) AS "{c}"')
        elif c in date_cols:
            select_list.append(
                f'CAST(TRY_STRPTIME("{c}", \'%Y%m%d\') AS DATE) AS "{c}"'
            )
        else:
            select_list.append(f'"{c}"')
    flag_list = [
        f'("{c}" IS NOT NULL AND TRY_STRPTIME("{c}", \'%Y%m%d\') IS NULL) '
        f'AS "__bad_{c}"'
        for c in date_cols
    ]

    tagged_alias = _unique_alias()
    tagged = rel.query(
        tagged_alias, f"SELECT {', '.join(select_list + flag_list)} FROM {tagged_alias}"
    )

    for c in date_cols:
        check_alias = _unique_alias()
        bad = tagged.query(
            check_alias, f'SELECT "primaryid" FROM {check_alias} WHERE "__bad_{c}"'
        ).fetchall()
        if bad:
            logger.warning(
                f"{table}.{c}: {len(bad)} row(s) didn't parse as 8-digit "
                f"date, nulled (primaryid(s): {[row[0] for row in bad][:10]})"
            )

    if not date_cols:
        return tagged
    exclude = ", ".join(f'"__bad_{c}"' for c in date_cols)
    final_alias = _unique_alias()
    return tagged.query(final_alias, f"SELECT * EXCLUDE ({exclude}) FROM {final_alias}")


if __name__ == "__main__":
    main()