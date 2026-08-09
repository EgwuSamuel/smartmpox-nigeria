-- ─────────────────────────────────────────────────────────────
-- FEATURE STORE
-- Pre-computed ML feature matrix — state × epiweek
-- Refreshed every Monday by Airflow before model inference
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS features_weekly (
    id                      SERIAL PRIMARY KEY,
    state_id                INT          NOT NULL REFERENCES ref_states(state_id),
    epi_year                INT          NOT NULL,
    epi_week                INT          NOT NULL,
    week_start_date         DATE         NOT NULL,

    -- ── CASE LAG FEATURES ────────────────────────────────────
    cases_t1                INT,         -- confirmed cases 1 week ago
    cases_t2                INT,         -- confirmed cases 2 weeks ago
    cases_t4                INT,         -- confirmed cases 4 weeks ago
    cases_rolling4w_mean    NUMERIC(8,2),-- 4-week rolling mean
    cases_rolling8w_mean    NUMERIC(8,2),-- 8-week rolling mean
    cases_log1p             NUMERIC(8,4),-- log(1 + cases_t1) for normalisation

    -- ── CLIMATE FEATURES (lagged) ─────────────────────────────
    rainfall_t2_mm          NUMERIC(7,2),-- rainfall 2 weeks ago
    rainfall_t4_mm          NUMERIC(7,2),-- rainfall 4 weeks ago
    temp_mean_t1_c          NUMERIC(5,2),
    ndvi_t4_mean            NUMERIC(5,4),-- NDVI 4 weeks ago (vegetation peak precedes hunting)
    ndvi_anomaly_t4         NUMERIC(5,4),

    -- ── RESERVOIR / ONE HEALTH FEATURES ─────────────────────
    reservoir_risk_index    NUMERIC(5,4),-- rodent habitat suitability (annual, repeated)
    forest_cover_pct        NUMERIC(5,2),-- current year forest cover
    deforestation_alert_cnt INT,         -- GLAD alerts in last 52 weeks

    -- ── SOCIAL MEDIA SIGNAL ──────────────────────────────────
    social_signal_zscore    NUMERIC(6,3),
    social_alert_flag       BOOLEAN      DEFAULT FALSE,
    social_mpox_posts_t1    INT,

    -- ── SPATIAL / STRUCTURAL FEATURES ────────────────────────
    is_border_state         BOOLEAN      DEFAULT FALSE,
    healthcare_access_mean  NUMERIC(5,3),-- mean across LGAs in state
    population_density      NUMERIC(10,2),

    -- ── REGIONAL FEATURES ────────────────────────────────────
    neighbour_cases_t1      INT,         -- sum of confirmed cases in adjacent states t-1

    -- ── TARGET VARIABLE (for training) ───────────────────────
    -- Is there elevated mpox activity 4 weeks from now? (binary)
    target_outbreak_4w      BOOLEAN,     -- NULL for future weeks (inference mode)
    target_cases_4w         INT,         -- actual case count 4 weeks ahead (for regression)

    -- ── METADATA ─────────────────────────────────────────────
    is_complete             BOOLEAN      DEFAULT FALSE, -- all features populated
    missing_features        TEXT[],                    -- list of NULL feature names
    computed_at             TIMESTAMPTZ  DEFAULT NOW(),

    UNIQUE (state_id, epi_year, epi_week)
);

CREATE INDEX IF NOT EXISTS idx_features_state_week ON features_weekly(state_id, epi_year, epi_week);
CREATE INDEX IF NOT EXISTS idx_features_target     ON features_weekly(target_outbreak_4w);
CREATE INDEX IF NOT EXISTS idx_features_complete   ON features_weekly(is_complete);
