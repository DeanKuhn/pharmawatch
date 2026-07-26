-- FAERS staging schema: one table per FAERS table, canonical (post-schema.py)
-- column names, real Postgres types where the data supports it.
--
-- Date columns: fda_dt/init_fda_dt/mfr_dt are consistently full 8-digit dates
-- (verified against 2019q1) and are typed `date`. event_dt/rept_dt/exp_dt/
-- start_dt/end_dt/death_dt can be partial precision (bare year or year-month
-- -- e.g. event_dt in 2019q1 is only ~65% full YYYYMMDD, the rest YYYY or
-- YYYYMM) and are kept as `text` rather than fabricating a day that was never
-- reported. Precision is still recoverable losslessly from the string's
-- length (4/6/8 chars) whenever a query needs it.
--
-- Legacy-only columns (image, confid, death_dt on demo) exist so pre-2012q4
-- quarters aren't silently dropped; they're simply null on modern quarters.
-- 2014q3-onward-only columns (auth_num, lit_ref, age_grp, prod_ai,
-- drug_rec_act) are null on quarters before that boundary.

CREATE TABLE IF NOT EXISTS demo (
    primaryid bigint NOT NULL,
    caseid bigint NOT NULL,
    caseversion int,
    i_f_code text,
    event_dt text,
    mfr_dt date,
    init_fda_dt date,
    fda_dt date,
    rept_cod text,
    auth_num text,
    mfr_num text,
    mfr_sndr text,
    lit_ref text,
    age double precision,
    age_cod text,
    age_grp text,
    sex text,
    e_sub text,
    wt double precision,
    wt_cod text,
    rept_dt text,
    to_mfr text,
    occp_cod text,
    reporter_country text,
    occr_country text,
    image text,
    confid text,
    death_dt text,
    PRIMARY KEY (primaryid)
);

CREATE TABLE IF NOT EXISTS drug (
    primaryid bigint NOT NULL,
    caseid bigint,
    drug_seq int,
    role_cod text,
    drugname text,
    prod_ai text,
    val_vbm text,
    route text,
    dose_vbm text,
    cum_dose_chr text,
    cum_dose_unit text,
    dechal text,
    rechal text,
    lot_num text,
    exp_dt text,
    nda_num text,
    dose_amt text,
    dose_unit text,
    dose_form text,
    dose_freq text
);

CREATE TABLE IF NOT EXISTS indi (
    primaryid bigint NOT NULL,
    caseid bigint,
    indi_drug_seq int,
    indi_pt text
);

CREATE TABLE IF NOT EXISTS outc (
    primaryid bigint NOT NULL,
    caseid bigint,
    outc_cod text
);

CREATE TABLE IF NOT EXISTS reac (
    primaryid bigint NOT NULL,
    caseid bigint,
    pt text,
    drug_rec_act text
);

CREATE TABLE IF NOT EXISTS rpsr (
    primaryid bigint NOT NULL,
    caseid bigint,
    rpsr_cod text
);

CREATE TABLE IF NOT EXISTS ther (
    primaryid bigint NOT NULL,
    caseid bigint,
    dsg_drug_seq int,
    start_dt text,
    end_dt text,
    dur text,
    dur_cod text
);

CREATE INDEX IF NOT EXISTS idx_drug_primaryid ON drug (primaryid);
CREATE INDEX IF NOT EXISTS idx_indi_primaryid ON indi (primaryid);
CREATE INDEX IF NOT EXISTS idx_outc_primaryid ON outc (primaryid);
CREATE INDEX IF NOT EXISTS idx_reac_primaryid ON reac (primaryid);
CREATE INDEX IF NOT EXISTS idx_rpsr_primaryid ON rpsr (primaryid);
CREATE INDEX IF NOT EXISTS idx_ther_primaryid ON ther (primaryid);
