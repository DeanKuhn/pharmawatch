import datetime
from pathlib import Path

import duckdb  # type:ignore
import polars as pl # type:ignore
import pytest # type:ignore

from faers.dedup import keep_primaryids, apply_dedup, CHILD_TABLES
from faers.load import (
    cast_canonical_types,
    sync_quarters_to_r2,
    load_table_across_quarters,
    _resolve_quarter_source,
    FAERS_TABLES
)
from faers.manifest import has_stage
from faers.r2 import R2Config


def to_relation(df: pl.DataFrame) -> duckdb.DuckDBPyRelation:
    """Wrap a Polars DataFrame fixture as the DuckDB relation
    cast_canonical_types now expects. DuckDB's replacement scan picks up
    `df` by variable name.
    """
    return duckdb.sql("SELECT * FROM df")


UNUSED_R2_CONFIG = R2Config(
    endpoint_url="unused", access_key_id="unused",
    secret_access_key="unused", bucket="unused",
)


class TestLoadTablesAcrossQuarters:
    def test_df_concat_shape_success(self, tmp_path):
        """2010q1's demo has no lit_ref column at all (real 2014q3+ addition,
        not a missing-value case). UNION ALL BY NAME must fill the gap with
        null rather than erroring on the mismatched schemas. Both quarters
        are local here -- no R2/network involved.
        """
        _write_quarter_parquet(tmp_path, "2010q1", "demo",
            pl.DataFrame({"primaryid": ["1"], "caseid": ["1"]}))
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame(
                {"primaryid": ["2"], "caseid": ["2"],
                 "lit_ref": ["Smith et al 2015"]}
            ))

        con = duckdb.connect()
        result = load_table_across_quarters(
            con, "demo", ["2010q1", "2019q1"], tmp_path, UNUSED_R2_CONFIG
        ).pl()

        assert result.height == 2
        assert set(result.columns) == {"primaryid", "caseid", "lit_ref"}
        assert result.filter(pl.col("primaryid") == "1")["lit_ref"].to_list() \
            == [None]
        assert result.filter(pl.col("primaryid") == "2")["lit_ref"].to_list() \
            == ["Smith et al 2015"]


class TestResolveQuarterSource:
    """load_table_across_quarters delegates the local-vs-R2 routing decision
    to _resolve_quarter_source. Tested standalone: DuckDB's httpfs actually
    opening an s3:// URI is real network I/O, not something a unit test
    should trigger (see docs/personal/duckdb_integration_plan.md's testing
    discussion) -- only the *decision* of which path string to hand DuckDB
    is ours to verify here.
    """

    def test_missing_local_quarter_resolves_to_r2_raw_zone_uri(self):
        """2010q1's local file was deleted after an earlier sync pushed it to
        R2's raw/ zone -- resolution must point at that key, not skip or
        error. (Whether DuckDB's httpfs can actually fetch it is exercised by
        the real archive sync, not a unit test.)
        """
        source = _resolve_quarter_source(
            "demo", "2010q1", Path("/nonexistent"), UNUSED_R2_CONFIG
        )
        assert source == "s3://unused/faers/raw/2010q1/demo.parquet"

    def test_present_local_quarter_is_preferred_over_r2(self, tmp_path):
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame({"primaryid": ["1"], "caseid": ["1"]}))

        source = _resolve_quarter_source(
            "demo", "2019q1", tmp_path, UNUSED_R2_CONFIG
        )
        assert source == str(tmp_path / "2019q1" / "demo.parquet")


def _write_quarter_parquet(
    parquet_dir, quarter: str, table: str, df: pl.DataFrame
) -> None:
    quarter_dir = parquet_dir / quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(quarter_dir / f"{table}.parquet")


def _write_minimal_child_tables(
    parquet_dir, quarter: str, primaryid: int
) -> None:
    """Write a throwaway single-row {"primaryid": [primaryid]} file for every
    table in CHILD_TABLES (drug/reac/indi/outc/rpsr/ther) at this quarter.
    Written as a string -- real raw Parquet always has primaryid as string
    (parse.py never infers types), and dedup.py's _sql_in_list assumes that.
    """
    for table in CHILD_TABLES:
        _write_quarter_parquet(
            parquet_dir, quarter, table,
            pl.DataFrame({"primaryid": [str(primaryid)]}
        ))


@pytest.fixture
def uploaded(monkeypatch, tmp_path):
    """Records every (key, df) sync_quarters_to_r2 would upload, instead of
    touching the network.

    Also chdir's into tmp_path: has_stage/mark_stage default to the relative
    path "data/manifest.json", so without this every test here would write
    real entries into the project manifest (happened once -- see the
    2004q1/2004q2/2019q1/2019q2 cleanup).
    """
    monkeypatch.chdir(tmp_path)
    store: dict[str, pl.DataFrame] = {}
    monkeypatch.setattr(
        "faers.load.upload_parquet",
        lambda df, key, config: store.__setitem__(key, df),
    )
    return store

@pytest.fixture
def upload_call_counts(monkeypatch, tmp_path):
    """Counts upload_parquet calls per key. See `uploaded` above for why
    this also chdir's into tmp_path.
    """
    monkeypatch.chdir(tmp_path)
    counts: dict[str, int] = {}
    monkeypatch.setattr(
        "faers.load.upload_parquet",
        lambda df, key, config: counts.__setitem__(key, counts.get(key, 0) + 1),
    )
    return counts


class TestSyncQuartersToR2:
    def test_canonical_upload_is_deduped_across_quarters(
        self, tmp_path, uploaded
    ):
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame(
                {"primaryid": ["1"], "caseid": ["500"], "caseversion": ["1"]}
            ))
        _write_minimal_child_tables(tmp_path, "2019q1", 1)

        _write_quarter_parquet(tmp_path, "2019q2", "demo",
            pl.DataFrame(
                {"primaryid": ["2"], "caseid": ["500"], "caseversion": ["2"]}
            ))
        _write_minimal_child_tables(tmp_path, "2019q2", 2)

        sync_quarters_to_r2(["2019q1", "2019q2"], tmp_path, config=R2Config(
            endpoint_url="unused", access_key_id="unused",
            secret_access_key="unused", bucket="unused",
        ))
        assert uploaded["faers/canonical/demo.parquet"].pl()["primaryid"].to_list() \
            == [2]

    def test_uploads_one_canonical_object_per_faers_table(
        self, tmp_path, uploaded
    ):
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame(
                {"primaryid": ["1"], "caseid": ["1"], "caseversion": ["1"]}
            ))
        _write_minimal_child_tables(tmp_path, "2019q1", 1)

        sync_quarters_to_r2(["2019q1"], tmp_path, config=R2Config(
            endpoint_url="unused", access_key_id="unused",
            secret_access_key="unused", bucket="unused",
        ))

        for table in FAERS_TABLES:
            assert f"faers/canonical/{table}.parquet" in uploaded

    def test_second_run_does_not_reupload_raw_but_reuploads_canonical(
        self, tmp_path, upload_call_counts
    ):
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame(
                {"primaryid": ["1"], "caseid": ["1"], "caseversion": ["1"]}
            ))
        _write_minimal_child_tables(tmp_path, "2019q1", 1)

        config = R2Config(endpoint_url="unused", access_key_id="unused",
                        secret_access_key="unused", bucket="unused")
        sync_quarters_to_r2(["2019q1"], tmp_path, config=config)
        sync_quarters_to_r2(["2019q1"], tmp_path, config=config)

        assert upload_call_counts["faers/canonical/demo.parquet"] == 2
        assert upload_call_counts["faers/raw/2019q1/demo.parquet"] == 1

    def test_missing_local_raw_file_with_unmarked_stage_does_not_reupload(
        self, tmp_path, upload_call_counts, monkeypatch
    ):
        """Simulates a crash: upload_parquet(demo) succeeded but the process
        died before mark_stage recorded "uploaded_raw", and the local file is
        (correctly) already gone. The raw-upload loop must confirm the object
        exists via download_parquet rather than crash or blindly re-upload.

        This is a test of the raw-upload loop specifically (lines in
        sync_quarters_to_r2 after the canonical dedup/upload step), which
        still calls download_parquet directly -- unaffected by the DuckDB/
        httpfs union work. But sync_quarters_to_r2 also runs the *canonical*
        union first, which for a genuinely-missing local file would ask
        DuckDB's httpfs to fetch demo/2019q1 from R2 for real. Rather than
        hit real network, _resolve_quarter_source is patched to point the
        canonical union at a same-shape stand-in file instead of an s3://
        URI -- this test's concern is the raw-upload loop's re-upload logic,
        not R2 fetch itself (see TestResolveQuarterSource for that).
        """
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame(
                {"primaryid": ["1"], "caseid": ["1"], "caseversion": ["1"]}
            ))
        _write_minimal_child_tables(tmp_path, "2019q1", 1)
        r2_mirror = tmp_path / "_r2_mirror_demo.parquet"
        (tmp_path / "2019q1" / "demo.parquet").rename(r2_mirror)

        from faers.load import _resolve_quarter_source as real_resolve

        def fake_resolve(table, quarter, parquet_dir, config):
            if (table, quarter) == ("demo", "2019q1"):
                return str(r2_mirror)
            return real_resolve(table, quarter, parquet_dir, config)

        monkeypatch.setattr("faers.load._resolve_quarter_source", fake_resolve)

        download_calls = []
        monkeypatch.setattr(
            "faers.load.download_parquet",
            lambda key, config: download_calls.append(key) or pl.DataFrame(
                {"primaryid": ["1"], "caseid": ["1"], "caseversion": ["1"]}
            ),
        )

        config = R2Config(endpoint_url="unused", access_key_id="unused",
                        secret_access_key="unused", bucket="unused")
        sync_quarters_to_r2(["2019q1"], tmp_path, config=config)

        assert "faers/raw/2019q1/demo.parquet" not in upload_call_counts
        assert download_calls.count("faers/raw/2019q1/demo.parquet") >= 1
        assert has_stage("2019q1", "uploaded_raw", "demo")


class TestCastForStaging:
    def test_bigint_columns_cast_to_int64(self):
        df = pl.DataFrame(
            {"primaryid": ["1"], "caseid": ["2"], "drugname": ["ASPIRIN"]}
        )
        result = cast_canonical_types(to_relation(df), "drug").pl()
        assert result.schema["primaryid"] == pl.Int64
        assert result.schema["caseid"] == pl.Int64
        assert result["primaryid"].to_list() == [1]
        assert result["caseid"].to_list() == [2]

    def test_caseversion_cast_to_int32(self):
        df = pl.DataFrame(
            {"primaryid": ["1"], "caseid": ["2"], "caseversion": ["3"]}
        )
        result = cast_canonical_types(to_relation(df), "demo").pl()
        assert result.schema["caseversion"] == pl.Int32
        assert result["caseversion"].to_list() == [3]

    def test_drug_seq_variants_cast_to_int32_per_table(self):
        drug = cast_canonical_types(
            to_relation(pl.DataFrame({"primaryid": ["1"], "drug_seq": ["1"]})), "drug"
        ).pl()
        indi = cast_canonical_types(
            to_relation(pl.DataFrame({"primaryid": ["1"], "indi_drug_seq": ["1"]})), "indi"
        ).pl()
        ther = cast_canonical_types(
            to_relation(pl.DataFrame({"primaryid": ["1"], "dsg_drug_seq": ["1"]})), "ther"
        ).pl()
        assert drug.schema["drug_seq"] == pl.Int32
        assert indi.schema["indi_drug_seq"] == pl.Int32
        assert ther.schema["dsg_drug_seq"] == pl.Int32

    def test_numeric_columns_cast_to_float64(self):
        df = pl.DataFrame({"primaryid": ["1"], "age": ["45.5"], "wt": ["70"]})
        result = cast_canonical_types(to_relation(df), "demo").pl()
        assert result.schema["age"] == pl.Float64
        assert result.schema["wt"] == pl.Float64
        assert result["age"].to_list() == [45.5]
        assert result["wt"].to_list() == [70.0]

    def test_date_columns_cast_to_date_with_correct_value(self):
        df = pl.DataFrame({
            "primaryid": ["1"],
            "mfr_dt": ["20190115"],
            "init_fda_dt": ["20190116"],
            "fda_dt": ["20190117"],
        })
        result = cast_canonical_types(to_relation(df), "demo").pl()
        assert result.schema["mfr_dt"] == pl.Date
        assert result["mfr_dt"].to_list() == [datetime.date(2019, 1, 15)]
        assert result["init_fda_dt"].to_list() == [datetime.date(2019, 1, 16)]
        assert result["fda_dt"].to_list() == [datetime.date(2019, 1, 17)]

    def test_text_columns_are_untouched(self):
        df = pl.DataFrame(
            {"primaryid": ["1"], "drugname": ["ASPIRIN"], "route": ["ORAL"]}
        )
        result = cast_canonical_types(to_relation(df), "drug").pl()
        assert result.schema["drugname"] == pl.Utf8
        assert result.schema["route"] == pl.Utf8
        assert result["drugname"].to_list() == ["ASPIRIN"]

    def test_empty_string_becomes_null_instead_of_erroring(self):
        df = pl.DataFrame(
            {"primaryid": ["1"], "caseid": ["2"],
             "caseversion": [""], "age": [""]}
        )
        result = cast_canonical_types(to_relation(df), "demo").pl()
        assert result["caseversion"].to_list() == [None]
        assert result["age"].to_list() == [None]

    def test_partial_precision_date_becomes_null_and_is_not_fabricated(self):
        df = pl.DataFrame({"primaryid": ["1"], "mfr_dt": ["2019"]})
        result = cast_canonical_types(to_relation(df), "demo").pl()
        assert result["mfr_dt"].to_list() == [None]

    def test_unparseable_date_logs_a_warning_naming_the_primaryid(self, caplog):
        df = pl.DataFrame({"primaryid": ["999"], "mfr_dt": ["bad"]})
        with caplog.at_level("WARNING"):
            cast_canonical_types(to_relation(df), "demo")
        assert any(
            "demo.mfr_dt" in r.message and "999"
            in r.message for r in caplog.records
        )

    def test_valid_dates_do_not_log_a_warning(self, caplog):
        df = pl.DataFrame({"primaryid": ["1"], "mfr_dt": ["20190115"]})
        with caplog.at_level("WARNING"):
            cast_canonical_types(to_relation(df), "demo")
        assert caplog.records == []

    def test_table_with_no_matching_cast_columns_is_unchanged(self):
        df = pl.DataFrame({"foo": ["bar"]})
        result = cast_canonical_types(to_relation(df), "not_a_real_table").pl()
        assert result.columns == ["foo"]
        assert result.schema["foo"] == pl.Utf8
        assert result["foo"].to_list() == ["bar"]

    def test_missing_mapped_column_does_not_error(self):
        df = pl.DataFrame({"primaryid": ["1"], "drugname": ["ASPIRIN"]})
        result = cast_canonical_types(to_relation(df), "drug").pl()
        assert "caseid" not in result.columns
        assert result.schema["primaryid"] == pl.Int64

    def test_all_faers_tables_cast_primaryid_and_caseid(self):
        from faers.load import FAERS_TABLES

        for table in FAERS_TABLES:
            df = pl.DataFrame({"primaryid": ["1"], "caseid": ["2"]})
            result = cast_canonical_types(to_relation(df), table).pl()
            assert result.schema["primaryid"] == pl.Int64
            assert result.schema["caseid"] == pl.Int64
