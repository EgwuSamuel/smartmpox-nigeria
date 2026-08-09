-- ════════════════════════════════════════════════════════════════
-- Migration 009: P3 Social Media Signals table
-- ════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS social_media_signals (
    id                  BIGSERIAL PRIMARY KEY,
    platform            TEXT NOT NULL,           -- 'rss_news' | 'reddit' | 'promed' | 'who'
    source_name         TEXT,                    -- e.g. 'Premium Times Nigeria', 'r/Nigeria'
    source_url          TEXT,                    -- canonical article/post URL
    published_at        TIMESTAMPTZ,             -- original post/publish timestamp
    scraped_at          TIMESTAMPTZ DEFAULT NOW(),
    title               TEXT,
    content_snippet     TEXT,                    -- first 600 chars of body text
    full_text           TEXT,                    -- full raw text (for re-analysis)
    detected_language   TEXT,                    -- ISO 639-1: 'en','pcm','ha','yo','ig','fr','unknown'
    is_mpox_relevant    BOOLEAN,                 -- TRUE if keyword/NLP flags mpox content
    relevance_score     FLOAT,                   -- 0–1 classifier confidence
    keyword_matched     TEXT[],                  -- which keywords triggered the pre-filter
    misinformation_flags TEXT[],                 -- themes: 'vaccine_blame','denial','bioweapon', etc.
    geo_mentions        TEXT[],                  -- Nigerian states/regions found in text
    state_id            INT REFERENCES ref_states(state_id),  -- primary state (if unambiguous)
    sentiment           TEXT CHECK (sentiment IN ('positive','negative','neutral','unknown')),
    UNIQUE (platform, source_url)                -- dedup on (platform, url)
);

CREATE INDEX IF NOT EXISTS idx_signals_published    ON social_media_signals (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_relevant     ON social_media_signals (is_mpox_relevant, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_platform     ON social_media_signals (platform);
CREATE INDEX IF NOT EXISTS idx_signals_language     ON social_media_signals (detected_language);
CREATE INDEX IF NOT EXISTS idx_signals_misinfo      ON social_media_signals USING GIN (misinformation_flags);
CREATE INDEX IF NOT EXISTS idx_signals_state        ON social_media_signals (state_id);

COMMENT ON TABLE social_media_signals IS
  'P3 passive surveillance: RSS news, Reddit, ProMED, WHO signals processed by NLP pipeline.';
