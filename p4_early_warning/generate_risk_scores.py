"""
P4 Task 04 — Risk Score Generation
Loads the trained XGBoost model, computes outbreak probabilities and SHAP values
for all states, and writes to risk_scores_weekly.

Run: python p4_early_warning/generate_risk_scores.py [--year 2024] [--week 1]
     python p4_early_warning/generate_risk_scores.py          # all available rows

Writes one row per (state, epi_year, epi_week) to risk_scores_weekly with:
  - risk_prob: XGBoost outbreak probability
  - risk_tier: green/amber/red/critical
  - top_feature_1..3: top SHAP drivers
  - cusum_signal: whether CUSUM z-score > 2.5
"""
import os, json, argparse
import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

MODEL_PATH = "p4_early_warning/models/xgb_outbreak_v1.json"
YOUDEN_THRESHOLD = 0.3452  # from train_model.py v1 output (16 features + velocity)

FEATURE_COLS = [
    "cases_t1", "cases_t2", "cases_t4",
    "cases_rolling4w_mean", "cases_rolling8w_mean", "cases_log1p",
    "cases_velocity", "cases_accel",
    "rainfall_t2_mm", "rainfall_t4_mm", "temp_mean_t1_c",
    "reservoir_risk_index",
    "is_border_state", "neighbour_cases_t1",
    "week_sin", "week_cos",
]

# Risk tier thresholds (tuned to Youden threshold at red/critical boundary)
TIER_THRESHOLDS = {
    "critical": YOUDEN_THRESHOLD,  # ≥ Youden → alert
    "red":      0.20,
    "amber":    0.08,
    # else → green
}


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def assign_tier(prob: float) -> str:
    if prob >= TIER_THRESHOLDS["critical"]: return "critical"
    if prob >= TIER_THRESHOLDS["red"]:      return "red"
    if prob >= TIER_THRESHOLDS["amber"]:    return "amber"
    return "green"


def load_feature_rows(conn, year=None, week=None) -> pd.DataFrame:
    where = "WHERE is_complete = TRUE"
    params = []
    if year:
        where += " AND epi_year = %s"
        params.append(year)
        if week:
            where += " AND epi_week = %s"
            params.append(week)

    sql = f"""
        SELECT state_id, epi_year, epi_week, week_start_date,
               cases_t1, cases_t2, cases_t4,
               cases_rolling4w_mean, cases_rolling8w_mean, cases_log1p,
               rainfall_t2_mm, rainfall_t4_mm, temp_mean_t1_c,
               reservoir_risk_index,
               is_border_state::INT AS is_border_state,
               neighbour_cases_t1
        FROM features_weekly {where}
        ORDER BY epi_year, epi_week, state_id
    """
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()

    # Derived features (must match train_model.py)
    df["week_sin"]       = np.sin(2 * np.pi * df["epi_week"] / 52)
    df["week_cos"]       = np.cos(2 * np.pi * df["epi_week"] / 52)
    df["cases_velocity"] = df["cases_t1"] - df["cases_t2"]
    df["cases_accel"]    = (df["cases_t1"] - df["cases_t2"]) - (df["cases_t2"] - df["cases_t4"]) / 2
    return df


def load_cusum_alerts(conn, year: int) -> dict:
    """Load CUSUM alerts from CSV if available; else return empty dict."""
    csv_path = f"p4_early_warning/models/cusum_alerts_{year}.csv"
    if not os.path.exists(csv_path):
        return {}
    df = pd.read_csv(csv_path)
    return {(row.state_id, row.epi_year, row.epi_week): bool(row.alert)
            for _, row in df.iterrows()}


def get_source_id(cur, model_version="v1") -> str:
    return model_version  # Used in the model_version column, not a FK


def upsert_risk_scores(cur, rows: list):
    sql = """
        INSERT INTO risk_scores_weekly (
            state_id, epi_year, epi_week, week_start_date,
            risk_prob, risk_tier, model_version, model_type,
            top_feature_1, top_feature_1_shap,
            top_feature_2, top_feature_2_shap,
            top_feature_3, top_feature_3_shap,
            cusum_signal
        ) VALUES %s
        ON CONFLICT (state_id, epi_year, epi_week, model_type) DO UPDATE SET
            risk_prob       = EXCLUDED.risk_prob,
            risk_tier       = EXCLUDED.risk_tier,
            model_version   = EXCLUDED.model_version,
            top_feature_1   = EXCLUDED.top_feature_1,
            top_feature_1_shap = EXCLUDED.top_feature_1_shap,
            top_feature_2   = EXCLUDED.top_feature_2,
            top_feature_2_shap = EXCLUDED.top_feature_2_shap,
            top_feature_3   = EXCLUDED.top_feature_3,
            top_feature_3_shap = EXCLUDED.top_feature_3_shap,
            cusum_signal    = EXCLUDED.cusum_signal,
            computed_at     = NOW()
    """
    execute_values(cur, sql, rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year",  type=int, default=None)
    parser.add_argument("--week",  type=int, default=None)
    parser.add_argument("--no-shap", action="store_true",
                        help="Skip SHAP computation (faster)")
    args = parser.parse_args()

    print("=== Risk Score Generation ===")

    import xgboost as xgb
    model = xgb.XGBClassifier()
    model.load_model(MODEL_PATH)
    print(f"  Model loaded: {MODEL_PATH}")

    conn = get_conn()
    df = load_feature_rows(conn, args.year, args.week)
    print(f"  Feature rows: {len(df)}")

    X = df[FEATURE_COLS].values.astype(np.float32)
    probs = model.predict_proba(X)[:, 1]
    df["risk_prob"] = probs
    df["risk_tier"] = [assign_tier(p) for p in probs]

    tier_counts = df["risk_tier"].value_counts()
    print(f"  Tiers: {dict(tier_counts)}")

    # SHAP values
    shap_values = None
    if not args.no_shap:
        try:
            import shap
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X)
            print(f"  SHAP computed for {len(df)} rows")
        except Exception as e:
            print(f"  SHAP failed: {e} — proceeding without SHAP")

    # CUSUM alerts
    cusum_map = {}
    if args.year:
        cusum_map = load_cusum_alerts(conn, args.year)

    # Build rows for DB insert
    db_rows = []
    for i, row in df.iterrows():
        idx = df.index.get_loc(i)

        # Top SHAP features
        top1_name = top1_shap = top2_name = top2_shap = top3_name = top3_shap = None
        if shap_values is not None:
            sv = shap_values[idx]
            top_idx = np.argsort(np.abs(sv))[::-1][:3]
            names = [FEATURE_COLS[j] for j in top_idx]
            vals  = [float(sv[j])    for j in top_idx]
            top1_name, top1_shap = names[0], round(vals[0], 4)
            top2_name, top2_shap = names[1], round(vals[1], 4) if len(names) > 1 else (None, None)
            top3_name, top3_shap = names[2], round(vals[2], 4) if len(names) > 2 else (None, None)

        cusum = cusum_map.get((row.state_id, row.epi_year, row.epi_week), False)

        db_rows.append((
            row.state_id, row.epi_year, row.epi_week, row.week_start_date,
            round(float(row.risk_prob), 4), row.risk_tier, "v1", "xgboost",
            top1_name, top1_shap, top2_name, top2_shap, top3_name, top3_shap,
            cusum,
        ))

    cur = conn.cursor()
    upsert_risk_scores(cur, db_rows)
    conn.commit()
    print(f"  Upserted {len(db_rows)} rows to risk_scores_weekly")
    cur.close()
    conn.close()

    # Print alert summary
    alerts = df[df["risk_tier"].isin(["red", "critical"])]
    if not alerts.empty:
        print(f"\n  High-risk rows: {len(alerts)}")
        by_state = alerts.groupby("state_id")["risk_prob"].max().sort_values(ascending=False)
        print("  Top alerting states:")
        print(by_state.head(5).to_string())


if __name__ == "__main__":
    main()
