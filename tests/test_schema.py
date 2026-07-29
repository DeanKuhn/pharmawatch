from faers.schema import canonical_rename_map


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
