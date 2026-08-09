-- ─────────────────────────────────────────────────────────────
-- ENVIRONMENTAL & CLIMATE DATA
-- ERA5 climate · MODIS NDVI · Rodent habitat · Forest cover
-- ─────────────────────────────────────────────────────────────

-- Weekly climate summary per state (from ERA5 gridded → state aggregation)
CREATE TABLE IF NOT EXISTS climate_weekly (
    id                  SERIAL PRIMARY KEY,
    state_id            INT          NOT NULL REFERENCES ref_states(state_id),
    epi_year            INT          NOT NULL,
    epi_week            INT          NOT NULL,
    week_start_date     DATE         NOT NULL,

    -- Temperature (°C, ERA5 2m temperature)
    temp_mean_c         NUMERIC(5,2),
    temp_max_c          NUMERIC(5,2),
    temp_min_c          NUMERIC(5,2),

    -- Precipitation (mm/week total, ERA5 total precipitation)
    rainfall_mm         NUMERIC(7,2),

    -- Humidity (%, ERA5 relative humidity)
    humidity_mean_pct   NUMERIC(5,2),

    -- Vegetation (MODIS NDVI 250m, state mean, 0–1 scale)
    ndvi_mean           NUMERIC(5,4),
    ndvi_anomaly        NUMERIC(5,4),                -- deviation from 5-year mean same week

    source              VARCHAR(20)  DEFAULT 'ERA5',
    ingested_at         TIMESTAMPTZ  DEFAULT NOW(),

    UNIQUE (state_id, epi_year, epi_week)
);

CREATE INDEX IF NOT EXISTS idx_climate_state_week ON climate_weekly(state_id, epi_year, epi_week);

-- Rodent habitat suitability (annual, state and LGA level)
-- Computed from MaxEnt model trained on GBIF occurrence records
CREATE TABLE IF NOT EXISTS habitat_suitability (
    id                  SERIAL PRIMARY KEY,
    spatial_unit        VARCHAR(10)  NOT NULL CHECK (spatial_unit IN ('state','lga')),
    state_id            INT          REFERENCES ref_states(state_id),
    lga_id              INT          REFERENCES ref_lgas(lga_id),
    year                INT          NOT NULL,

    -- Cricetomys gambianus (giant pouched rat — primary mpox reservoir)
    cricetomys_suit     NUMERIC(5,4),                -- MaxEnt suitability score 0–1
    cricetomys_ci_low   NUMERIC(5,4),
    cricetomys_ci_high  NUMERIC(5,4),

    -- Funisciurus spp. (rope squirrel — secondary reservoir)
    funisciurus_suit    NUMERIC(5,4),

    -- Combined one-health risk index
    reservoir_risk_index NUMERIC(5,4) GENERATED ALWAYS AS (
                            COALESCE(cricetomys_suit, 0) * 0.7 +
                            COALESCE(funisciurus_suit, 0) * 0.3
                        ) STORED,

    model_version       VARCHAR(20),
    gbif_record_count   INT,                         -- GBIF records used to fit model
    ingested_at         TIMESTAMPTZ  DEFAULT NOW()
);

-- Forest cover change (annual, LGA level — from Global Forest Watch)
CREATE TABLE IF NOT EXISTS forest_cover_annual (
    id                  SERIAL PRIMARY KEY,
    lga_id              INT          NOT NULL REFERENCES ref_lgas(lga_id),
    year                INT          NOT NULL,
    tree_cover_pct      NUMERIC(5,2),
    tree_cover_loss_ha  NUMERIC(10,2),               -- Hansen tree cover loss
    deforestation_alert_count INT    DEFAULT 0,       -- GLAD/RADD alerts in year
    ingested_at         TIMESTAMPTZ  DEFAULT NOW(),

    UNIQUE (lga_id, year)
);
