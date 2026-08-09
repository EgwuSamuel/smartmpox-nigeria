-- ─────────────────────────────────────────────────────────────
-- DATA QUALITY & PIPELINE AUDIT TABLES
-- Required for the DQ scorecard (P1 Task 04) and paper methods
-- ─────────────────────────────────────────────────────────────

-- ETL pipeline run log
CREATE TABLE IF NOT EXISTS etl_run_log (
    run_id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id           INT          NOT NULL REFERENCES ref_data_sources(source_id),
    run_started_at      TIMESTAMPTZ  NOT NULL,
    run_finished_at     TIMESTAMPTZ,
    status              VARCHAR(15)  NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running','success','partial','failed')),
    rows_fetched        INT          DEFAULT 0,
    rows_inserted       INT          DEFAULT 0,
    rows_updated        INT          DEFAULT 0,
    rows_rejected       INT          DEFAULT 0,
    error_message       TEXT,
    airflow_dag_run_id  VARCHAR(200)
);

-- Data quality scorecard (per source per month — for paper Methods section)
CREATE TABLE IF NOT EXISTS dq_scorecard (
    id                  SERIAL PRIMARY KEY,
    source_id           INT          NOT NULL REFERENCES ref_data_sources(source_id),
    score_year          INT          NOT NULL,
    score_month         INT          NOT NULL,

    -- Completeness: % of expected fields populated
    completeness_pct    NUMERIC(5,2),

    -- Timeliness: median days from event to data availability
    timeliness_days_p50 NUMERIC(6,1),

    -- Consistency: % agreement with cross-validation source
    consistency_pct     NUMERIC(5,2),
    cross_validated_against VARCHAR(30),  -- other source used for consistency check

    -- Coverage: % of 37 jurisdictions with data this month
    jurisdiction_coverage_pct NUMERIC(5,2),

    -- Overall score (weighted average)
    overall_score       NUMERIC(5,2),

    computed_at         TIMESTAMPTZ  DEFAULT NOW(),
    notes               TEXT,

    UNIQUE (source_id, score_year, score_month)
);

-- OWID vs NCDC concordance (monthly — publishable finding)
CREATE TABLE IF NOT EXISTS concordance_owid_ncdc (
    id                  SERIAL PRIMARY KEY,
    year                INT          NOT NULL,
    month               INT          NOT NULL,
    ncdc_sitrep_total   INT,
    owid_total          INT,
    who_total           INT,
    gap_owid_minus_ncdc INT  GENERATED ALWAYS AS (owid_total - ncdc_sitrep_total) STORED,
    gap_pct             NUMERIC(6,2) GENERATED ALWAYS AS (
                            CASE WHEN ncdc_sitrep_total > 0
                            THEN ROUND((owid_total - ncdc_sitrep_total)::NUMERIC / ncdc_sitrep_total * 100, 2)
                            ELSE NULL END
                        ) STORED,
    bland_altman_mean   NUMERIC(8,2),
    bland_altman_diff   NUMERIC(8,2),
    computed_at         TIMESTAMPTZ  DEFAULT NOW(),

    UNIQUE (year, month)
);
