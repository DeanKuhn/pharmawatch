import polars as pl # type:ignore

from faers.schema import apply_schema, canonical_rename_map


class TestCanonicalRenameMap:
    def test_legacy_quarter_renames_demo_identity_and_descriptive_columns(self):
        assert canonical_rename_map("demo", "2004q1") == {
            "ISR": "primaryid",
            "CASE": "caseid",
            "FOLL_SEQ": "caseversion",
            "I_F_COD": "i_f_code",
            "GNDR_COD": "sex",
        }

    def test_pre_2014q3_modern_quarter_only_renames_descriptive_columns(self):
        assert canonical_rename_map("demo", "2013q1") == {"GNDR_COD": "sex"}

    def test_current_quarter_is_a_noop(self):
        assert canonical_rename_map("demo", "2019q1") == {}

    def test_indi_legacy_renames_drug_seq(self):
        assert canonical_rename_map("indi", "2004q1") == {
            "ISR": "primaryid",
            "DRUG_SEQ": "indi_drug_seq",
        }

    def test_ther_legacy_renames_drug_seq(self):
        assert canonical_rename_map("ther", "2004q1") == {
            "ISR": "primaryid",
            "DRUG_SEQ": "dsg_drug_seq",
        }

    def test_reac_has_no_descriptive_rename(self):
        assert canonical_rename_map("reac", "2004q1") == {"ISR": "primaryid"}


class TestApplySchema:
    def test_legacy_dataframe_gets_canonical_columns(self):
        df = pl.DataFrame({"ISR": ["1"], "CASE": ["2"], "FOLL_SEQ": ["0"], "AGE": ["45"]})
        result = apply_schema(df, "demo", "2004q1")
        assert result.columns == ["primaryid", "caseid", "caseversion", "age"]

    def test_modern_dataframe_is_unchanged(self):
        df = pl.DataFrame({"primaryid": ["1"], "caseid": ["2"], "caseversion": ["1"]})
        result = apply_schema(df, "demo", "2019q1")
        assert result.columns == ["primaryid", "caseid", "caseversion"]

    def test_legacy_dataframe_missing_a_column_does_not_error(self):
        df = pl.DataFrame({"ISR": ["1"], "CASE": ["2"]})
        result = apply_schema(df, "demo", "2004q1")
        assert result.columns == ["primaryid", "caseid"]

    def test_pre_2014q3_dataframe_renames_gndr_cod_to_sex(self):
        df = pl.DataFrame({"primaryid": ["1"], "GNDR_COD": ["F"]})
        result = apply_schema(df, "demo", "2013q1")
        assert result.columns == ["primaryid", "sex"]
