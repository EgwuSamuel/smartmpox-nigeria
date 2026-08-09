-- ════════════════════════════════════════════════════════════════
-- Migration 011: Admin review columns + authenticated RLS policies
-- ════════════════════════════════════════════════════════════════

-- Add admin review columns to cases_individual
ALTER TABLE cases_individual
    ADD COLUMN IF NOT EXISTS review_status  TEXT DEFAULT 'pending'
        CHECK (review_status IN ('pending','reviewed','flagged')),
    ADD COLUMN IF NOT EXISTS review_notes   TEXT,
    ADD COLUMN IF NOT EXISTS reviewed_by    TEXT,
    ADD COLUMN IF NOT EXISTS reviewed_at    TIMESTAMPTZ;

-- Index for admin dashboard queries
CREATE INDEX IF NOT EXISTS idx_cases_review_status ON cases_individual (review_status);
CREATE INDEX IF NOT EXISTS idx_cases_date_reported  ON cases_individual (date_reported DESC);

-- ── RLS policies for authenticated admins ─────────────────────
-- (anon INSERT + SELECT policies already exist from P2)

-- Admins can read all cases
DO $$
BEGIN
    DROP POLICY IF EXISTS admin_select_cases ON cases_individual;
    CREATE POLICY admin_select_cases ON cases_individual
        FOR SELECT TO authenticated USING (true);
END $$;

-- Admins can update review fields only
DO $$
BEGIN
    DROP POLICY IF EXISTS admin_update_cases ON cases_individual;
    CREATE POLICY admin_update_cases ON cases_individual
        FOR UPDATE TO authenticated
        USING (true)
        WITH CHECK (true);
END $$;

COMMENT ON COLUMN cases_individual.review_status IS 'Admin review status: pending | reviewed | flagged';
COMMENT ON COLUMN cases_individual.review_notes  IS 'Admin review notes / comments';
COMMENT ON COLUMN cases_individual.reviewed_by   IS 'Email of admin who reviewed the case';
COMMENT ON COLUMN cases_individual.reviewed_at   IS 'Timestamp of last admin review';
