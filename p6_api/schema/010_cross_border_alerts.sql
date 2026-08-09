-- ════════════════════════════════════════════════════════════════
-- Migration 010: P6 cross-border alerts + public API support
-- ════════════════════════════════════════════════════════════════

-- ── Cross-border alert log ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS cross_border_alerts (
    id                  BIGSERIAL PRIMARY KEY,
    generated_at        TIMESTAMPTZ DEFAULT NOW(),
    alert_week          INT   NOT NULL,              -- epi week
    alert_year          INT   NOT NULL,
    country_code        CHAR(3) NOT NULL,            -- ISO 3166-1 alpha-3: CMR, BEN, NER, TCD
    country_name        TEXT  NOT NULL,
    border_state_id     INT   REFERENCES ref_states(state_id),
    border_state_name   TEXT,
    risk_tier           TEXT  CHECK (risk_tier IN ('critical','red','amber','green')),
    risk_prob           NUMERIC(6,4),
    recommended_action  TEXT,                        -- 'Heighten surveillance' / 'Activate alert'
    alert_payload       JSONB,                       -- full JSON sent to country
    delivered_at        TIMESTAMPTZ,                 -- when alert was "delivered" (webhook/log)
    delivery_status     TEXT DEFAULT 'pending' CHECK (delivery_status IN ('pending','delivered','failed')),
    UNIQUE (alert_year, alert_week, country_code, border_state_id)
);

CREATE INDEX IF NOT EXISTS idx_cba_country    ON cross_border_alerts (country_code, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_cba_week       ON cross_border_alerts (alert_year, alert_week);
CREATE INDEX IF NOT EXISTS idx_cba_state      ON cross_border_alerts (border_state_id);
CREATE INDEX IF NOT EXISTS idx_cba_status     ON cross_border_alerts (delivery_status);

-- ── Public API views (anon-readable) ──────────────────────────

-- Current risk scores (37 states, most recent week)
CREATE OR REPLACE VIEW api_latest_risk AS
SELECT
    r.state_id,
    s.state_name,
    r.epi_year,
    r.epi_week,
    r.week_start_date,
    r.risk_prob,
    r.risk_tier,
    r.model_version,
    r.top_feature_1,
    r.top_feature_1_shap,
    r.top_feature_2,
    r.top_feature_2_shap,
    r.top_feature_3,
    r.top_feature_3_shap,
    r.cusum_signal,
    r.ears_signal
FROM latest_risk_scores r
JOIN ref_states s USING (state_id);

-- Cross-border alert summary (current week)
CREATE OR REPLACE VIEW api_cross_border_current AS
SELECT
    c.country_code,
    c.country_name,
    c.border_state_id,
    c.border_state_name,
    c.risk_tier,
    c.risk_prob,
    c.recommended_action,
    c.generated_at,
    c.delivery_status
FROM cross_border_alerts c
WHERE (c.alert_year, c.alert_week) = (
    SELECT alert_year, alert_week
    FROM cross_border_alerts
    ORDER BY generated_at DESC
    LIMIT 1
)
ORDER BY c.risk_prob DESC;

-- Weekly surveillance summary (public-facing)
CREATE OR REPLACE VIEW api_weekly_cases AS
SELECT
    sw.epi_year,
    sw.epi_week,
    sw.week_start_date,
    s.state_name,
    sw.state_id,
    sw.confirmed,
    sw.suspected,
    sw.total_cases,
    sw.deaths,
    sw.source_id
FROM surveillance_weekly sw
JOIN ref_states s USING (state_id)
ORDER BY sw.epi_year DESC, sw.epi_week DESC, sw.total_cases DESC;

-- ── RLS: allow anon reads on API views and new table ──────────
ALTER TABLE cross_border_alerts ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS anon_select_cba ON cross_border_alerts;
    CREATE POLICY anon_select_cba ON cross_border_alerts
        FOR SELECT TO anon USING (true);
END $$;

COMMENT ON TABLE cross_border_alerts IS
  'P6 cross-border alert log — alerts sent to neighboring countries when Nigeria border states hit critical/red risk tiers.';
