-- ─────────────────────────────────────────────────────────────
-- MODEL OUTPUT TABLES
-- Risk scores, alerts, resource recommendations
-- ─────────────────────────────────────────────────────────────

-- Weekly risk scores from the early warning model (P4)
CREATE TABLE IF NOT EXISTS risk_scores_weekly (
    id                  SERIAL PRIMARY KEY,
    state_id            INT          NOT NULL REFERENCES ref_states(state_id),
    epi_year            INT          NOT NULL,
    epi_week            INT          NOT NULL,
    week_start_date     DATE         NOT NULL,

    -- Predicted probability of elevated mpox activity in next 4 weeks
    risk_prob           NUMERIC(6,4) NOT NULL,        -- 0.0 – 1.0
    risk_prob_lower     NUMERIC(6,4),                 -- 80% conformal prediction interval lower
    risk_prob_upper     NUMERIC(6,4),                 -- 80% conformal prediction interval upper

    -- Risk tier (for dashboard display and alert triggering)
    risk_tier           VARCHAR(10)  NOT NULL
                        CHECK (risk_tier IN ('green','amber','red','critical')),

    -- Model info
    model_version       VARCHAR(30),
    model_type          VARCHAR(20), -- xgboost / lstm / tft / ensemble

    -- Top SHAP features (for explainability in dashboard)
    top_feature_1       VARCHAR(50),
    top_feature_1_shap  NUMERIC(8,4),
    top_feature_2       VARCHAR(50),
    top_feature_2_shap  NUMERIC(8,4),
    top_feature_3       VARCHAR(50),
    top_feature_3_shap  NUMERIC(8,4),

    -- CUSUM / EARS parallel output
    cusum_signal        BOOLEAN      DEFAULT FALSE,
    ears_signal         BOOLEAN      DEFAULT FALSE,

    computed_at         TIMESTAMPTZ  DEFAULT NOW(),

    UNIQUE (state_id, epi_year, epi_week, model_type)
);

-- Alert log
CREATE TABLE IF NOT EXISTS alerts_log (
    alert_id            UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    state_id            INT          NOT NULL REFERENCES ref_states(state_id),
    lga_id              INT          REFERENCES ref_lgas(lga_id),

    alert_tier          INT          NOT NULL CHECK (alert_tier IN (1,2,3)),
    alert_type          VARCHAR(30)  NOT NULL, -- model_risk / social_signal / aberration / case_threshold
    alert_message       TEXT         NOT NULL,

    triggered_at        TIMESTAMPTZ  DEFAULT NOW(),

    -- Delivery tracking
    sms_sent            BOOLEAN      DEFAULT FALSE,
    sms_delivered_at    TIMESTAMPTZ,
    email_sent          BOOLEAN      DEFAULT FALSE,
    email_delivered_at  TIMESTAMPTZ,
    whatsapp_sent       BOOLEAN      DEFAULT FALSE,

    -- Response tracking
    acknowledged        BOOLEAN      DEFAULT FALSE,
    acknowledged_at     TIMESTAMPTZ,
    acknowledged_by     VARCHAR(100),
    resolution_notes    TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_state ON alerts_log(state_id);
CREATE INDEX IF NOT EXISTS idx_alerts_tier  ON alerts_log(alert_tier);
CREATE INDEX IF NOT EXISTS idx_alerts_time  ON alerts_log(triggered_at);
