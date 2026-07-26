import datetime

import polars as pl # type:ignore
import pytest # type:ignore

from faers.dedup import keep_primaryids, apply_dedup, CHILD_TABLES
from faers.load import cast_canonical_types, sync_quarters_to_r2, load_table_across_quarters, FAERS_TABLES
from faers.r2 import R2Config


UNUSED_R2_CONFIG = R2Config(
    endpoint_url="unused", access_key_id="unused",
    secret_access_key="unused", bucket="unused",
)


class TestLoadTablesAcrossQuarters:
    def test_df_concat_shape_success(self, tmp_path):
        """2010q1's demo.parquet has no lit_ref column at all -- it's a real
        2014q3+-only addition (schema.py's DESCRIPTIVE_RENAME docstring) with
        no legacy equivalent to rename from, not a missing-value case.
        pl.concat(how="diagonal") must fill that gap with null rather than
        erroring on the two quarters' mismatched schemas.
        """
        _write_quarter_parquet(tmp_path, "2010q1", "demo",
            pl.DataFrame({"primaryid": [1], "caseid": [1]}))
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame({"primaryid": [2], "caseid": [2], "lit_ref": ["Smith et al 2015"]}))

        result = load_table_across_quarters("demo", ["2010q1", "2019q1"], tmp_path, UNUSED_R2_CONFIG)

        assert result.height == 2
        assert set(result.columns) == {"primaryid", "caseid", "lit_ref"}
        assert result.filter(pl.col("primaryid") == 1)["lit_ref"].to_list() == [None]
        assert result.filter(pl.col("primaryid") == 2)["lit_ref"].to_list() == ["Smith et al 2015"]

    def test_missing_local_quarter_falls_back_to_r2_raw(self, tmp_path, monkeypatch):
        """2010q1's demo.parquet was deleted locally after an earlier sync already
        pushed it to R2's raw/ zone (the normal "local is scratch" lifecycle).
        This quarter must still be fetched -- from R2, not skipped or errored --
        since dedup needs every quarter present on every run.
        """
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame({"primaryid": [2], "caseid": [2]}))
        # 2010q1 intentionally has no local file written.

        fetched_keys = []

        def fake_download_parquet(key, config):
            fetched_keys.append(key)
            return pl.DataFrame({"primaryid": [1], "caseid": [1]})

        monkeypatch.setattr("faers.load.download_parquet", fake_download_parquet)

        result = load_table_across_quarters("demo", ["2010q1", "2019q1"], tmp_path, UNUSED_R2_CONFIG)

        assert fetched_keys == ["faers/raw/2010q1/demo.parquet"]
        assert result.height == 2
        assert set(result["primaryid"].to_list()) == {1, 2}


def _write_quarter_parquet(parquet_dir, quarter: str, table: str, df: pl.DataFrame) -> None:
    quarter_dir = parquet_dir / quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(quarter_dir / f"{table}.parquet")


def _write_minimal_child_tables(parquet_dir, quarter: str, primaryid: int) -> None:
    """Write a throwaway single-row {"primaryid": [primaryid]} file for every
    table in CHILD_TABLES (drug/reac/indi/outc/rpsr/ther) at this quarter."""
    for table in CHILD_TABLES:
        _write_quarter_parquet(parquet_dir, quarter, table, pl.DataFrame({"primaryid": [primaryid]}))


@pytest.fixture
def uploaded(monkeypatch, tmp_path):
    """Records every (key, df) sync_quarters_to_r2 would otherwise send to
    R2, instead of touching the network.

    Also chdir's into tmp_path -- manifest.py's has_stage/mark_stage default
    to the *relative* path "data/manifest.json", and sync_quarters_to_r2
    calls them with no override. Without this, every test in
    TestSyncQuartersToR2 would write real uploaded_raw entries into the
    real project manifest (this happened once already -- see the
    2004q1/2004q2/2019q1/2019q2 cleanup). Requesting tmp_path here (the same
    function-scoped object pytest hands the test itself) means any test
    depending on this fixture gets the chdir for free, without needing to
    remember to add it per-test.
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
    """Counts how many times upload_parquet was called per key. See
    `uploaded` above for why this also chdir's into tmp_path.
    """
    monkeypatch.chdir(tmp_path)
    counts: dict[str, int] = {}
    monkeypatch.setattr(
        "faers.load.upload_parquet",
        lambda df, key, config: counts.__setitem__(key, counts.get(key, 0) + 1),
    )
    return counts


class TestSyncQuartersToR2:
    def test_canonical_upload_is_deduped_across_quarters(self, tmp_path, uploaded):
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame({"primaryid": [1], "caseid": [500], "caseversion": [1]}))
        _write_minimal_child_tables(tmp_path, "2019q1", 1)

        _write_quarter_parquet(tmp_path, "2019q2", "demo",
            pl.DataFrame({"primaryid": [2], "caseid": [500], "caseversion": [2]}))
        _write_minimal_child_tables(tmp_path, "2019q2", 2)

        sync_quarters_to_r2(["2019q1", "2019q2"], tmp_path, config=R2Config(
            endpoint_url="unused", access_key_id="unused",
            secret_access_key="unused", bucket="unused",
        ))
        assert uploaded["faers/canonical/demo.parquet"]["primaryid"].to_list() == [2]

    def test_uploads_one_canonical_object_per_faers_table(self, tmp_path, uploaded):
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame({"primaryid": [1], "caseid": [1], "caseversion": [1]}))
        _write_minimal_child_tables(tmp_path, "2019q1", 1)

        sync_quarters_to_r2(["2019q1"], tmp_path, config=R2Config(
            endpoint_url="unused", access_key_id="unused",
            secret_access_key="unused", bucket="unused",
        ))

        for table in FAERS_TABLES:
            assert f"faers/canonical/{table}.parquet" in uploaded

    def test_second_run_does_not_reupload_raw_but_reuploads_canonical(self, tmp_path, upload_call_counts):
        _write_quarter_parquet(tmp_path, "2019q1", "demo",
            pl.DataFrame({"primaryid": [1], "caseid": [1], "caseversion": [1]}))
        _write_minimal_child_tables(tmp_path, "2019q1", 1)

        config = R2Config(endpoint_url="unused", access_key_id="unused",
                        secret_access_key="unused", bucket="unused")
        sync_quarters_to_r2(["2019q1"], tmp_path, config=config)
        sync_quarters_to_r2(["2019q1"], tmp_path, config=config)

        assert upload_call_counts["faers/canonical/demo.parquet"] == 2
        assert upload_call_counts["faers/raw/2019q1/demo.parquet"] == 1


class TestCastForStaging:
    def test_bigint_columns_cast_to_int64(self):
        df = pl.DataFrame({"primaryid": ["1"], "caseid": ["2"], "drugname": ["ASPIRIN"]})
        result = cast_canonical_types(df, "drug")
        assert result.schema["primaryid"] == pl.Int64
        assert result.schema["caseid"] == pl.Int64
        assert result["primaryid"].to_list() == [1]
        assert result["caseid"].to_list() == [2]

    def test_caseversion_cast_to_int32(self):
        df = pl.DataFrame({"primaryid": ["1"], "caseid": ["2"], "caseversion": ["3"]})
        result = cast_canonical_types(df, "demo")
        assert result.schema["caseversion"] == pl.Int32
        assert result["caseversion"].to_list() == [3]

    def test_drug_seq_variants_cast_to_int32_per_table(self):
        drug = cast_canonical_types(pl.DataFrame({"primaryid": ["1"], "drug_seq": ["1"]}), "drug")
        indi = cast_canonical_types(pl.DataFrame({"primaryid": ["1"], "indi_drug_seq": ["1"]}), "indi")
        ther = cast_canonical_types(pl.DataFrame({"primaryid": ["1"], "dsg_drug_seq": ["1"]}), "ther")
        assert drug.schema["drug_seq"] == pl.Int32
        assert indi.schema["indi_drug_seq"] == pl.Int32
        assert ther.schema["dsg_drug_seq"] == pl.Int32

    def test_numeric_columns_cast_to_float64(self):
        df = pl.DataFrame({"primaryid": ["1"], "age": ["45.5"], "wt": ["70"]})
        result = cast_canonical_types(df, "demo")
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
        result = cast_canonical_types(df, "demo")
        assert result.schema["mfr_dt"] == pl.Date
        assert result["mfr_dt"].to_list() == [datetime.date(2019, 1, 15)]
        assert result["init_fda_dt"].to_list() == [datetime.date(2019, 1, 16)]
        assert result["fda_dt"].to_list() == [datetime.date(2019, 1, 17)]

    def test_text_columns_are_untouched(self):
        df = pl.DataFrame({"primaryid": ["1"], "drugname": ["ASPIRIN"], "route": ["ORAL"]})
        result = cast_canonical_types(df, "drug")
        assert result.schema["drugname"] == pl.Utf8
        assert result.schema["route"] == pl.Utf8
        assert result["drugname"].to_list() == ["ASPIRIN"]

    def test_empty_string_becomes_null_instead_of_erroring(self):
        df = pl.DataFrame({"primaryid": ["1"], "caseid": ["2"], "caseversion": [""], "age": [""]})
        result = cast_canonical_types(df, "demo")
        assert result["caseversion"].to_list() == [None]
        assert result["age"].to_list() == [None]

    def test_partial_precision_date_becomes_null_and_is_not_fabricated(self):
        df = pl.DataFrame({"primaryid": ["1"], "mfr_dt": ["2019"]})
        result = cast_canonical_types(df, "demo")
        assert result["mfr_dt"].to_list() == [None]

    def test_unparseable_date_logs_a_warning_naming_the_primaryid(self, caplog):
        df = pl.DataFrame({"primaryid": ["999"], "mfr_dt": ["bad"]})
        with caplog.at_level("WARNING"):
            cast_canonical_types(df, "demo")
        assert any("demo.mfr_dt" in r.message and "999" in r.message for r in caplog.records)

    def test_valid_dates_do_not_log_a_warning(self, caplog):
        df = pl.DataFrame({"primaryid": ["1"], "mfr_dt": ["20190115"]})
        with caplog.at_level("WARNING"):
            cast_canonical_types(df, "demo")
        assert caplog.records == []

    def test_table_with_no_matching_cast_columns_is_unchanged(self):
        df = pl.DataFrame({"foo": ["bar"]})
        result = cast_canonical_types(df, "not_a_real_table")
        assert result.columns == ["foo"]
        assert result.schema["foo"] == pl.Utf8
        assert result["foo"].to_list() == ["bar"]

    def test_missing_mapped_column_does_not_error(self):
        df = pl.DataFrame({"primaryid": ["1"], "drugname": ["ASPIRIN"]})
        result = cast_canonical_types(df, "drug")
        assert "caseid" not in result.columns
        assert result.schema["primaryid"] == pl.Int64

    def test_all_faers_tables_cast_primaryid_and_caseid(self):
        from faers.load import FAERS_TABLES

        for table in FAERS_TABLES:
            df = pl.DataFrame({"primaryid": ["1"], "caseid": ["2"]})
            result = cast_canonical_types(df, table)
            assert result.schema["primaryid"] == pl.Int64
            assert result.schema["caseid"] == pl.Int64
