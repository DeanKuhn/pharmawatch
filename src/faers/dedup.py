"""Collapse FAERS case-versions down to one row per real-world case.

Consumes tables already run through schema.py's apply_schema, so identity
columns are canonical (primaryid/caseid/caseversion) regardless of era.
Quarter-agnostic by design (see CLAUDE.md's 2026-07-19 decision): callers
concatenate DEMO (and each other table) across as many parsed quarters as
they have on hand before calling these functions, so cross-quarter dedup
falls out of the input, not extra logic here.
"""

import polars as pl # type:ignore
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def configure_logging(log_path: Path = Path("logs/dedup.log")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler()
        ]
    )


def keep_primaryids(demo: pl.DataFrame) -> pl.Series:
    """Group DEMO by caseid, keep the primaryid of the max-caseversion row per case.

    demo can be a single quarter's DEMO table or several quarters concatenated
    (see CLAUDE.md's 2026-07-19 decision) -- this function has no notion of
    quarter, it only ever looks at primaryid/caseid/caseversion.

    caseversion is cast to int locally: it arrives as a string (parse.py infers
    no types), and "9" > "10" under string comparison would silently pick the
    wrong winner. Casting here, not upstream, keeps schema.py/parse.py's
    no-typing convention intact -- this cast is dedup.py's own, disposable,
    not committed to the DataFrame callers hold.
    """
    demo = demo.with_columns(pl.col("caseversion").cast(pl.Int64).alias("caseversion_int"))
    demo_grouped = demo.group_by("caseid").agg(
        pl.all().exclude("caseid").sort_by("caseversion_int").last(),
        pl.col("caseversion_int").eq(pl.col("caseversion_int").max()).sum().alias("tie_count")
    )

    ties = demo_grouped.filter(pl.col("tie_count") > 1)
    if ties.height > 0:
        raise ValueError(
            f"caseid(s) {ties['caseid'].to_list()} have multiple primaryids "
            "tied at the same max caseversion -- no clean winner, needs "
            "investigation before a resolution rule is decided (see "
            "CLAUDE.md/README mess log)."
        )
    return demo_grouped["primaryid"]


def apply_dedup(
    tables: dict[str, pl.DataFrame],
    keep: pl.Series
) -> dict[str, pl.DataFrame]:
    """Filter every table to rows whose primaryid is in keep."""
    result = {}
    for name, df in tables.items():
        filtered = df.filter(pl.col("primaryid").is_in(keep.to_list()))
        logger.info(f"{name}: kept {filtered.height}/{df.height} rows after dedup")
        result[name] = filtered
    return result