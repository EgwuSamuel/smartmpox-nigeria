"""
P4 Task 09 — Generate Deployed Expert-Adjusted Tiers
====================================================
Applies the knowledge-based expert inference layer to the deployed XGBoost risk
scores and writes the escalation-adjusted tiers back to risk_scores_weekly under
model_type='expert_system', then (re)creates the latest_expert_scores view the
dashboard reads.

The engine's knowledge-base thresholds are fitted on labelled history (no
leakage); the escalation is applied on top of the already-stored XGBoost tier so
the dashboard's expert view stays consistent with its ML view.

Run:  python p4_early_warning/generate_expert_scores.py
"""
import os
import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

from expert_system import load_features, build_engine, base_tier

load_dotenv()


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def load_stored_xgb(conn) -> pd.DataFrame:
    """Deployed XGBoost rows joined to the features needed to evaluate the rules."""
    cur = conn.cursor()
    cur.execute("""
        SELECT r.state_id, r.epi_year, r.epi_week, r.week_start_date,
               r.risk_prob, r.risk_tier,
               f.reservoir_risk_index,
               f.is_border_state::INT AS is_border_state,
               f.neighbour_cases_t1,
               f.cases_t1, f.cases_t2, f.cases_rolling4w_mean
        FROM risk_scores_weekly r
        JOIN features_weekly f
          ON f.state_id = r.state_id AND f.epi_year = r.epi_year AND f.epi_week = r.epi_week
        WHERE r.model_type = 'xgboost'
    """)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    df["cases_velocity"] = df["cases_t1"].astype(float) - df["cases_t2"].astype(float)
    return df


def upsert_expert(cur, rows):
    sql = """
        INSERT INTO risk_scores_weekly (
            state_id, epi_year, epi_week, week_start_date,
            risk_prob, risk_tier, model_version, model_type, top_feature_1
        ) VALUES %s
        ON CONFLICT (state_id, epi_year, epi_week, model_type) DO UPDATE SET
            risk_prob     = EXCLUDED.risk_prob,
            risk_tier     = EXCLUDED.risk_tier,
            model_version = EXCLUDED.model_version,
            top_feature_1 = EXCLUDED.top_feature_1,
            computed_at   = NOW()
    """
    execute_values(cur, sql, rows)


VIEW_SQL = """
CREATE OR REPLACE VIEW latest_expert_scores AS
SELECT DISTINCT ON (state_id)
    id, state_id, epi_year, epi_week, week_start_date,
    risk_prob, risk_tier, model_version, model_type,
    top_feature_1 AS expert_reason, computed_at
FROM risk_scores_weekly
WHERE model_type = 'expert_system'
ORDER BY state_id, epi_year DESC, epi_week DESC;

GRANT SELECT ON latest_expert_scores TO anon, authenticated;
"""


def main():
    print("=== Generate Expert-Adjusted Tiers ===")
    conn = get_conn()

    # Build engine on all labelled history (fits youden, reservoir p75/p90, digital surge)
    labelled = load_features()
    train_max = int(labelled["epi_year"].max())
    _model, engine, meta = build_engine(labelled, train_max)
    print(f"  Engine thresholds: {meta}")

    df = load_stored_xgb(conn)
    print(f"  Loaded {len(df)} deployed XGBoost rows")

    rows, n_escalated = [], 0
    tier_counts = {"green": 0, "amber": 0, "red": 0, "critical": 0}
    for _, r in df.iterrows():
        prob = float(r["risk_prob"])
        dec = engine.infer_tier(r, prob, stored_base_tier=r["risk_tier"])
        if dec["escalated"]:
            n_escalated += 1
        tier_counts[dec["expert_tier"]] += 1
        reason = dec["reason"][:50] if dec["reason"] else None
        rows.append((
            int(r["state_id"]), int(r["epi_year"]), int(r["epi_week"]), r["week_start_date"],
            round(prob, 4), dec["expert_tier"], "expert_v1", "expert_system",
            reason,
        ))

    cur = conn.cursor()
    upsert_expert(cur, rows)
    conn.commit()
    print(f"  Upserted {len(rows)} expert rows | escalated {n_escalated} "
          f"({100*n_escalated/len(rows):.1f}%)")
    print(f"  Expert tier distribution: {tier_counts}")

    # (Re)create the dashboard view + refresh PostgREST schema cache
    cur.execute(VIEW_SQL)
    conn.commit()
    try:
        cur.execute("NOTIFY pgrst, 'reload schema';")
        conn.commit()
    except Exception as e:
        print(f"  (schema reload notify skipped: {e})")
    print("  View latest_expert_scores (re)created + granted to anon")

    # Report the latest-week escalations (what the dashboard will show)
    cur.execute("""
        SELECT state_id, risk_tier, expert_reason
        FROM latest_expert_scores
        WHERE expert_reason IS NOT NULL
        ORDER BY state_id
    """)
    esc = cur.fetchall()
    print(f"\n  Latest-week escalated states: {len(esc)}")
    for sid, tier, reason in esc:
        print(f"    state {sid}: → {tier}  ({reason})")

    cur.close(); conn.close()


if __name__ == "__main__":
    main()
