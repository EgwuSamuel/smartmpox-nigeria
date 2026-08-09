-- ─────────────────────────────────────────────────────────────
-- SOCIAL MEDIA & DIGITAL SURVEILLANCE TABLES
-- Raw posts + NLP classifications + infodemic signals
-- ─────────────────────────────────────────────────────────────

-- Raw social media and news posts
CREATE TABLE IF NOT EXISTS social_raw_posts (
    post_id             UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform            VARCHAR(20)  NOT NULL    -- twitter / facebook / news_punch / news_vanguard / ...
                        CHECK (platform IN ('twitter','facebook','news_punch','news_vanguard',
                                            'news_guardian_ng','news_premium_times',
                                            'news_channels','news_nan','other')),
    source_post_id      VARCHAR(100),            -- original platform ID

    -- Content
    post_text           TEXT         NOT NULL,
    post_language       CHAR(6),                 -- en / pcm (Pidgin) / ha / yo / ig
    post_url            TEXT,

    -- Metadata
    post_timestamp      TIMESTAMPTZ,
    author_id           VARCHAR(100),            -- anonymised / hashed
    location_raw        TEXT,                    -- raw location string from platform

    -- Detected state mention (from NLP geo-extraction)
    state_id_detected   INT          REFERENCES ref_states(state_id),
    lga_id_detected     INT          REFERENCES ref_lgas(lga_id),

    -- NLP outputs (populated by P3 pipeline)
    is_mpox_relevant    BOOLEAN,
    relevance_score     NUMERIC(5,4),            -- classifier confidence 0–1
    report_type         VARCHAR(30)              -- symptom_report / cluster_report /
                                                 -- misinformation / general_news /
                                                 -- healthcare_demand / rumour
                        CHECK (report_type IN ('symptom_report','cluster_report',
                               'misinformation','general_news','healthcare_demand',
                               'rumour','unclassified')),
    report_type_score   NUMERIC(5,4),

    -- Topic (BERTopic output)
    topic_id            INT,
    topic_label         VARCHAR(100),

    ingested_at         TIMESTAMPTZ  DEFAULT NOW(),
    classified_at       TIMESTAMPTZ,
    raw_json            JSONB                    -- full original API response
);

CREATE INDEX IF NOT EXISTS idx_social_platform   ON social_raw_posts(platform);
CREATE INDEX IF NOT EXISTS idx_social_timestamp  ON social_raw_posts(post_timestamp);
CREATE INDEX IF NOT EXISTS idx_social_relevant   ON social_raw_posts(is_mpox_relevant);
CREATE INDEX IF NOT EXISTS idx_social_state      ON social_raw_posts(state_id_detected);
CREATE INDEX IF NOT EXISTS idx_social_text       ON social_raw_posts USING GIN(to_tsvector('english', post_text));

-- Weekly social media signal aggregation (state × epiweek)
-- Feeds into the ML feature matrix as social_signal_score
CREATE TABLE IF NOT EXISTS social_signal_weekly (
    id                  SERIAL PRIMARY KEY,
    state_id            INT          NOT NULL REFERENCES ref_states(state_id),
    epi_year            INT          NOT NULL,
    epi_week            INT          NOT NULL,
    week_start_date     DATE         NOT NULL,

    total_posts         INT          DEFAULT 0,
    mpox_relevant_posts INT          DEFAULT 0,
    symptom_reports     INT          DEFAULT 0,
    cluster_reports     INT          DEFAULT 0,
    misinformation_posts INT         DEFAULT 0,

    -- Signal score: z-score of relevant posts vs. rolling 8-week baseline
    signal_zscore       NUMERIC(6,3),
    signal_alert        BOOLEAN      DEFAULT FALSE,  -- TRUE when z-score > 2.0

    top_topic_label     VARCHAR(100),
    top_misinformation  VARCHAR(200),

    computed_at         TIMESTAMPTZ  DEFAULT NOW(),

    UNIQUE (state_id, epi_year, epi_week)
);
