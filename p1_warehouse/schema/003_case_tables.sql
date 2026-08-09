-- ─────────────────────────────────────────────────────────────
-- CASE DATA TABLES
-- Individual case records + aggregate weekly surveillance
-- ─────────────────────────────────────────────────────────────

-- Individual mpox case records (linelist)
CREATE TABLE IF NOT EXISTS cases_individual (
    case_id             UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id           INT          NOT NULL REFERENCES ref_data_sources(source_id),
    source_case_id      VARCHAR(60),                -- original ID from source system

    -- Classification
    case_classification VARCHAR(20)  NOT NULL        -- suspected / probable / confirmed / discarded
                        CHECK (case_classification IN ('suspected','probable','confirmed','discarded')),
    clade               VARCHAR(10),                 -- clade_I / clade_Ib / clade_II

    -- Dates
    date_onset          DATE,                        -- symptom onset
    date_reported       DATE,                        -- reported to LGA/state surveillance
    date_notified       DATE,                        -- notified to NCDC
    date_hospitalised   DATE,
    date_outcome        DATE,
    report_year         INT  GENERATED ALWAYS AS (EXTRACT(YEAR  FROM date_reported)::INT) STORED,
    report_epiweek      INT  GENERATED ALWAYS AS (EXTRACT(WEEK  FROM date_reported)::INT) STORED,

    -- Location (most specific available)
    country             CHAR(3)      DEFAULT 'NGA',
    state_id            INT          REFERENCES ref_states(state_id),
    lga_id              INT          REFERENCES ref_lgas(lga_id),
    location_precision  VARCHAR(20)  DEFAULT 'state'  -- state / lga / facility / gps
                        CHECK (location_precision IN ('state','lga','facility','gps')),
    geom                GEOMETRY(POINT, 4326),        -- GPS if available

    -- Demographics
    age_years           INT,
    age_group           VARCHAR(10)                  -- <5 / 5-14 / 15-29 / 30-44 / 45-59 / 60+
                        CHECK (age_group IN ('<5','5-14','15-29','30-44','45-59','60+','unknown')),
    sex                 CHAR(1)                      -- M / F / U
                        CHECK (sex IN ('M','F','U')),
    occupation          VARCHAR(60),                 -- farmer / hunter / healthcare / student / ...

    -- Clinical
    symptom_fever       BOOLEAN,
    symptom_rash        BOOLEAN,
    symptom_lymphadenopathy BOOLEAN,
    symptom_headache    BOOLEAN,
    symptom_myalgia     BOOLEAN,
    symptom_other       TEXT,
    days_onset_to_report INT  GENERATED ALWAYS AS (
                            (date_reported - date_onset)
                        ) STORED,

    -- Exposure & Transmission
    contact_type        VARCHAR(30),                 -- animal / human / unknown
    exposure_animal     BOOLEAN,
    exposure_bushmeat   BOOLEAN,
    travel_history      BOOLEAN,
    travel_country      VARCHAR(60),

    -- Lab
    lab_tested          BOOLEAN      DEFAULT FALSE,
    lab_method          VARCHAR(30),                 -- PCR / ELISA / culture
    lab_result          VARCHAR(15)                  -- positive / negative / pending
                        CHECK (lab_result IN ('positive','negative','pending','not_tested')),

    -- Outcome
    outcome             VARCHAR(15)                  -- alive / dead / unknown
                        CHECK (outcome IN ('alive','dead','unknown')),

    -- Vaccination
    vaccinated_smallpox BOOLEAN,

    -- Metadata
    data_quality_score  NUMERIC(3,2),                -- 0–1 completeness score
    ingested_at         TIMESTAMPTZ  DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  DEFAULT NOW(),
    raw_record          JSONB,                       -- original JSON from source
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_cases_state       ON cases_individual(state_id);
CREATE INDEX IF NOT EXISTS idx_cases_lga         ON cases_individual(lga_id);
CREATE INDEX IF NOT EXISTS idx_cases_date        ON cases_individual(date_reported);
CREATE INDEX IF NOT EXISTS idx_cases_epiweek     ON cases_individual(report_year, report_epiweek);
CREATE INDEX IF NOT EXISTS idx_cases_class       ON cases_individual(case_classification);
CREATE INDEX IF NOT EXISTS idx_cases_source      ON cases_individual(source_id);
CREATE INDEX IF NOT EXISTS idx_cases_geom        ON cases_individual USING GIST(geom);

-- ─────────────────────────────────────────────────────────────
-- Weekly aggregate surveillance (state × epiweek)
-- Populated by ETL from NCDC sitreps, OWID, WHO
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS surveillance_weekly (
    id                  SERIAL PRIMARY KEY,
    state_id            INT          NOT NULL REFERENCES ref_states(state_id),
    epi_year            INT          NOT NULL,
    epi_week            INT          NOT NULL CHECK (epi_week BETWEEN 1 AND 53),
    week_start_date     DATE         NOT NULL,

    -- Case counts
    suspected           INT          DEFAULT 0,
    probable            INT          DEFAULT 0,
    confirmed           INT          DEFAULT 0,
    discarded           INT          DEFAULT 0,
    deaths              INT          DEFAULT 0,

    -- Derived
    total_cases         INT  GENERATED ALWAYS AS (suspected + probable + confirmed) STORED,
    cfr_pct             NUMERIC(5,2) GENERATED ALWAYS AS (
                            CASE WHEN (suspected + probable + confirmed) > 0
                            THEN ROUND(deaths::NUMERIC / (suspected + probable + confirmed) * 100, 2)
                            ELSE NULL END
                        ) STORED,

    -- Source tracking (multiple sources per cell, last-write-wins with priority)
    source_id           INT          REFERENCES ref_data_sources(source_id),
    source_confirmed    INT          DEFAULT 0,      -- NCDC sitrep value
    owid_confirmed      INT,                         -- OWID value (for concordance analysis)
    who_confirmed       INT,                         -- WHO value
    owid_ncdc_gap       INT  GENERATED ALWAYS AS (owid_confirmed - source_confirmed) STORED,

    -- Data quality
    is_interpolated     BOOLEAN      DEFAULT FALSE,  -- gap-filled week
    completeness_flag   CHAR(1)      DEFAULT 'A'     -- A=complete / B=partial / C=estimated
                        CHECK (completeness_flag IN ('A','B','C')),

    ingested_at         TIMESTAMPTZ  DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  DEFAULT NOW(),

    UNIQUE (state_id, epi_year, epi_week)
);

CREATE INDEX IF NOT EXISTS idx_surv_state_week ON surveillance_weekly(state_id, epi_year, epi_week);
CREATE INDEX IF NOT EXISTS idx_surv_date       ON surveillance_weekly(week_start_date);
