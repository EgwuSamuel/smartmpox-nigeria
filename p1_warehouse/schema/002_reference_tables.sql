-- ─────────────────────────────────────────────────────────────
-- REFERENCE / LOOKUP TABLES
-- Static spatial and classification data loaded once
-- ─────────────────────────────────────────────────────────────

-- Nigerian states (36 + FCT = 37 jurisdictions)
CREATE TABLE IF NOT EXISTS ref_states (
    state_id        SERIAL PRIMARY KEY,
    state_code      CHAR(2)      NOT NULL UNIQUE,  -- NE, LA, KN, ...
    state_name      VARCHAR(60)  NOT NULL UNIQUE,
    geopolitical_zone VARCHAR(20) NOT NULL,         -- North-East, South-South, ...
    capital         VARCHAR(60),
    area_km2        NUMERIC(10,2),
    is_border_state BOOLEAN      DEFAULT FALSE,     -- shares international border
    border_countries VARCHAR(120),                  -- comma-separated country names
    geom            GEOMETRY(MULTIPOLYGON, 4326),   -- GRID3 boundary
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Nigerian LGAs (774 Local Government Areas)
CREATE TABLE IF NOT EXISTS ref_lgas (
    lga_id          SERIAL PRIMARY KEY,
    lga_code        VARCHAR(10)  NOT NULL UNIQUE,   -- GRID3 standard code
    lga_name        VARCHAR(100) NOT NULL,
    state_id        INT          NOT NULL REFERENCES ref_states(state_id),
    lga_type        VARCHAR(20),                    -- urban / peri-urban / rural
    population_2023 INT,                            -- WorldPop estimate
    area_km2        NUMERIC(10,2),
    forest_cover_pct NUMERIC(5,2),                  -- from Global Forest Watch
    healthcare_access_index NUMERIC(5,3),           -- 0–1, computed from GRID3 facility data
    is_border_lga   BOOLEAN      DEFAULT FALSE,
    geom            GEOMETRY(MULTIPOLYGON, 4326),   -- GRID3 boundary
    centroid        GEOMETRY(POINT, 4326),
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lgas_state ON ref_lgas(state_id);
CREATE INDEX IF NOT EXISTS idx_lgas_geom  ON ref_lgas USING GIST(geom);

-- Health facilities (from GRID3 Nigeria)
CREATE TABLE IF NOT EXISTS ref_facilities (
    facility_id     SERIAL PRIMARY KEY,
    facility_code   VARCHAR(20)  UNIQUE,
    facility_name   VARCHAR(200),
    lga_id          INT          REFERENCES ref_lgas(lga_id),
    state_id        INT          REFERENCES ref_states(state_id),
    facility_type   VARCHAR(50), -- Primary Health Centre / General Hospital / Teaching Hospital
    ownership       VARCHAR(30), -- Federal / State / LGA / Private / Mission
    has_lab         BOOLEAN      DEFAULT FALSE,
    has_isolation   BOOLEAN      DEFAULT FALSE,
    geom            GEOMETRY(POINT, 4326),
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_facilities_lga  ON ref_facilities(lga_id);
CREATE INDEX IF NOT EXISTS idx_facilities_geom ON ref_facilities USING GIST(geom);

-- Data sources registry
CREATE TABLE IF NOT EXISTS ref_data_sources (
    source_id       SERIAL PRIMARY KEY,
    source_code     VARCHAR(30)  NOT NULL UNIQUE,  -- NCDC_SITREP, OWID, GLOBAL_HEALTH, ...
    source_name     VARCHAR(100) NOT NULL,
    source_url      TEXT,
    licence         VARCHAR(60),                   -- MIT, CC-BY, Public, Fair Use
    update_frequency VARCHAR(20),                  -- daily / weekly / monthly / static
    requires_auth   BOOLEAN      DEFAULT FALSE,
    last_fetched_at TIMESTAMPTZ,
    notes           TEXT,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
