"""Dedup all FAERS quarterly reports and create 7 deduped canonical reports."""

import argparse
import logging
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)
log_path = Path("logs/deduped_reports.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)


def _build_keep_list(merged, con) -> str:
    cols = [
        row[0]
        for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{merged}/demo.parquet')"
        ).fetchall()
    ]

    richness = " + ".join(
        f'CASE WHEN "{c}" IS NOT NULL THEN 1 ELSE 0 END' for c in cols
    )

    con.execute(f"""
        CREATE TEMP TABLE raw AS (
            SELECT *, COALESCE(TRY_CAST(caseversion AS BIGINT), 0) AS caseversion_int
            FROM read_parquet('{merged}/demo.parquet')
        )
    """)
    raw_rows = con.execute("SELECT count(*) FROM raw").fetchall()[0][0]
    log.info(f"Raw table: {raw_rows} rows")

    con.execute("""
        CREATE TEMP TABLE max_ver AS (
            SELECT caseid, primaryid FROM raw
            QUALIFY caseversion_int = MAX(caseversion_int) OVER (PARTITION BY caseid)
        )
    """)
    maxver_rows = con.execute("SELECT count(*) FROM max_ver").fetchall()[0][0]
    log.info(f"Max_ver table: {maxver_rows} rows")

    con.execute("""
        CREATE TEMP TABLE untied AS (
            SELECT caseid, primaryid FROM max_ver
            QUALIFY COUNT(*) OVER (PARTITION BY caseid) = 1
        )
    """)
    untied_rows = con.execute("SELECT count(*) FROM untied").fetchall()[0][0]
    log.info(f"Untied table: {untied_rows} rows")

    con.execute("""
        CREATE TEMP TABLE tied AS (
            SELECT caseid, primaryid FROM max_ver
            QUALIFY COUNT(*) OVER (PARTITION BY caseid) > 1
        )
    """)
    tied_rows = con.execute("SELECT count(*) FROM tied").fetchall()[0][0]
    log.info(f"Tied table: {tied_rows} rows")

    # Step 1, count child rows per tied primaryid
    con.execute(f"""
        CREATE TEMP TABLE child_counts as (
            select primaryid, count(*) as n
            from (
                select primaryid from read_parquet('{merged}/drug.parquet') 
                semi join tied using (primaryid)
                union all
                select primaryid from read_parquet('{merged}/reac.parquet')
                semi join tied using (primaryid)
                union all
                select primaryid from read_parquet('{merged}/indi.parquet')
                semi join tied using (primaryid)
                union all
                select primaryid from read_parquet('{merged}/outc.parquet')
                semi join tied using (primaryid)
                union all
                select primaryid from read_parquet('{merged}/rpsr.parquet')
                semi join tied using (primaryid)
                union all
                select primaryid from read_parquet('{merged}/ther.parquet')
                semi join tied using (primaryid)
            )
            group by primaryid
        )
    """)
    childcounts_rows = con.execute("SELECT count(*) FROM child_counts").fetchall()[0][0]
    log.info(f"Child_counts table: {childcounts_rows} rows")

    # Step 2, count non-null fields per tied primaryid
    con.execute(f"""
        CREATE TEMP TABLE richness as (
            select primaryid, ({richness}) as nonnull_fields
            from raw
            semi join tied using (primaryid)
        )
    """)
    richness_rows = con.execute("SELECT count(*) FROM richness").fetchall()[0][0]
    log.info(f"Richness table: {richness_rows} rows")

    # Step 3, rank and pick winner
    con.execute("""
        CREATE TEMP TABLE tie_winners as (
            select caseid, primaryid
            from tied t
            left join child_counts c using (primaryid)
            left join richness r using (primaryid)
            qualify row_number() over (
                partition by caseid
                order by
                    coalesce(c.n, 0) desc,
                    coalesce(r.nonnull_fields, 0) desc,
                    try_cast(t.primaryid as bigint) asc,
                    primaryid asc
            ) = 1
        )
    """)
    tiewinners_rows = con.execute("SELECT count(*) FROM tie_winners").fetchall()[0][0]
    log.info(f"Tie_winners table: {tiewinners_rows} rows")

    con.execute("""
        CREATE TEMP TABLE keep AS (
            SELECT * FROM untied
            UNION ALL
            SELECT * FROM tie_winners
        )
    """)
    keep_rows = con.execute("SELECT count(*) FROM keep").fetchall()[0][0]
    log.info(f"Keep table: {keep_rows} rows")

    return richness


def dedup() -> None:
    parser = argparse.ArgumentParser(description="Dedup merged tables.")
    parser.add_argument(
        "--merged_dir", default="data/merged", help="location of merged tables"
    )
    parser.add_argument(
        "--deduped_dir", default="data/deduped", help="location of deduped tables"
    )
    args = parser.parse_args()

    merged_dir = Path(args.merged_dir)
    if not merged_dir.exists():
        log.error(
            f"Cannot find where merged files are kept, attempted to look in {merged_dir}"
        )
        return

    deduped_dir = Path(args.deduped_dir)
    deduped_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    tables = ["demo", "drug", "reac", "indi", "outc", "rpsr", "ther"]

    richness = _build_keep_list(merged_dir, con)

    for table in tables:
        if table == "demo":
            con.execute(f"""
                COPY (
                    WITH filtered AS (
                        SELECT DISTINCT *
                        FROM read_parquet('{merged_dir}/demo.parquet')
                        SEMI JOIN keep USING (caseid, primaryid)
                    )
                    SELECT * EXCLUDE (__conflict_rank)
                    FROM (
                        SELECT *,
                            ROW_NUMBER() OVER (
                                PARTITION BY primaryid
                                ORDER BY ({richness}) DESC
                            ) AS __conflict_rank
                        FROM filtered
                    )
                    WHERE __conflict_rank = 1
                ) TO '{deduped_dir}/demo.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
        else:
            con.execute(f"""
                COPY (
                    SELECT DISTINCT *
                    FROM read_parquet('{merged_dir}/{table}.parquet')
                    SEMI JOIN keep USING (primaryid)
                ) TO '{deduped_dir}/{table}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)

        rows = con.execute(
            f"SELECT num_rows FROM parquet_file_metadata('{deduped_dir}/{table}.parquet')"
        ).fetchall()[0][0]
        log.info(f"Deduped {table}: {rows} rows")


if __name__ == "__main__":
    dedup()
