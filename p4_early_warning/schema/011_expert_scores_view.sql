-- P4 migration 011 — latest_expert_scores view
-- Surfaces the knowledge-based expert-adjusted tiers (model_type='expert_system',
-- written by generate_expert_scores.py) to the dashboard via the anon REST role.
-- Mirrors latest_risk_scores but for the expert layer, and exposes the escalation
-- reason (which knowledge-base rule fired) for dashboard explainability.

CREATE OR REPLACE VIEW latest_expert_scores AS
SELECT DISTINCT ON (state_id)
    id, state_id, epi_year, epi_week, week_start_date,
    risk_prob, risk_tier, model_version, model_type,
    top_feature_1 AS expert_reason, computed_at
FROM risk_scores_weekly
WHERE model_type = 'expert_system'
ORDER BY state_id, epi_year DESC, epi_week DESC;

GRANT SELECT ON latest_expert_scores TO anon, authenticated;

-- Refresh PostgREST schema cache so the view is immediately queryable.
NOTIFY pgrst, 'reload schema';
