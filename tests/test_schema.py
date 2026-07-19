import polars as pl # type:ignore

from faers.schema import apply_schema, canonical_rename_map


class TestCanonicalRenameMap:
    def test_legacy_quarter_renames_identity_columns(self):
        assert canonical_rename_map("2004q1") == {
            "ISR": "primaryid",
            "CASE": "caseid",
            "FOLL_SEQ": "caseversion",
        }

    def test_modern_quarter_is_a_noop(self):
        assert canonical_rename_map("2013q1") == {}


class TestApplySchema:
    def test_legacy_dataframe_gets_canonical_columns(self):
        df = pl.DataFrame({"ISR": ["1"], "CASE": ["2"], "FOLL_SEQ": ["0"], "AGE": ["45"]})
        result = apply_schema(df, "2004q1")
        assert result.columns == ["primaryid", "caseid", "caseversion", "AGE"]

    def test_modern_dataframe_is_unchanged(self):
        df = pl.DataFrame({"primaryid": ["1"], "caseid": ["2"], "caseversion": ["1"]})
        result = apply_schema(df, "2013q1")
        assert result.columns == ["primaryid", "caseid", "caseversion"]

    def test_legacy_dataframe_missing_a_column_does_not_error(self):
        df = pl.DataFrame({"ISR": ["1"], "CASE": ["2"]})
        result = apply_schema(df, "2004q1")
        assert result.columns == ["primaryid", "caseid"]