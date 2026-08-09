"""
P4 Task 03 — CUSUM / EARS-C3 Anomaly Detector
Classic epidemiological threshold-based surveillance alert system.
Complements the XGBoost model as an independent baseline.

EARS-C3: CDC Early Aberration Reporting System method C3.
Alert when current week's count exceeds baseline mean + k*SD for 3 consecutive periods.

Run: python p4_early_warning/cusum_detector.py [--year 2024] [--k 3.0]

Outputs:
  - models/cusum_alerts_{year}.csv
  - Prints sensitivity/specificity vs XGBoost target
"""
import os, json, argparse
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from datetime import datetime, timezone
from sklearn.metrics import roc_auc_score, average_precision_score, confusion_matrix

load_dotenv()


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def load_surveillance(conn, test_year: int) -> pd.DataFrame:
    """Load surveillance counts and targets for CUSUM evaluation."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            fw.state_id, fw.epi_year, fw.epi_week, fw.week_start_date,
            fw.cases_t1,
            fw.target_outbreak_4w, fw.target_cases_4w
        FROM features_weekly fw
        WHERE fw.is_complete = TRUE
          AND fw.target_outbreak_4w IS NOT NULL
        ORDER BY fw.state_id, fw.epi_year, fw.epi_week
    """)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def compute_cusum(series: pd.Series, k: float = 3.0, baseline_weeks: int = 52) -> pd.Series:
    """
    CUSUM-EARS-C3: alert if current > baseline_mean + k * baseline_SD.
    Baseline = prior `baseline_weeks` weeks.
    Returns alert score = (current - mean) / SD  (positive = elevated).
    """
    scores = []
    arr = series.values
    for i in range(len(arr)):
        start = max(0, i - baseline_weeks)
        window = arr[start:i]
        if len(window) < 4:
            scores.append(0.0)
            continue
        mu = np.mean(window)
        sd = np.std(window, ddof=1)
        z = (arr[i] - mu) / sd if sd > 0 else 0.0
        scores.append(float(z))
    return pd.Series(scores, index=series.index)


def cusum_alert(z_scores: pd.Series, k: float, consecutive: int = 1) -> pd.Series:
    """Flag rows where z-score exceeds threshold (optionally for `consecutive` periods)."""
    above = (z_scores > k).astype(int)
    if consecutive <= 1:
        return above
    # Rolling window — alert if current AND prior (consecutive-1) weeks also above
    return above.rolling(consecutive).min().fillna(0).astype(int)


def evaluate_cusum(df: pd.DataFrame, alert_col: str, target_col: str = "target_outbreak_4w"):
    """Compute CUSUM performance metrics vs the surveillance target."""
    y_true = df[target_col].values.astype(int)
    y_pred = df[alert_col].values.astype(int)
    probs  = df[f"{alert_col}_score"].values  # z-scores as continuous signal

    roc_auc = roc_auc_score(y_true, probs) if y_true.sum() > 0 else None
    pr_auc  = average_precision_score(y_true, probs) if y_true.sum() > 0 else None

    cm = confusion_matrix(y_true, y_pred)
    if cm.shape == (2, 2):
        tn, fp, fn, tp = cm.ravel()
    else:
        tn, fp, fn, tp = int(cm[0, 0]), 0, 0, 0

    sens = tp / (tp + fn) if (tp + fn) > 0 else 0
    spec = tn / (tn + fp) if (tn + fp) > 0 else 0
    far  = fp / (fp + tn) if (fp + tn) > 0 else 0
    ppv  = tp / (tp + fp) if (tp + fp) > 0 else 0

    return {
        "n": int(len(y_true)), "n_pos": int(y_true.sum()),
        "roc_auc": round(roc_auc, 4) if roc_auc else None,
        "pr_auc":  round(pr_auc, 4)  if pr_auc  else None,
        "sensitivity": round(sens, 4),
        "specificity": round(spec, 4),
        "ppv":         round(ppv, 4),
        "false_alarm_rate": round(far, 4),
        "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year",        type=int,   default=2024,
                        help="Evaluation year (test period)")
    parser.add_argument("--k",           type=float, default=3.0,
                        help="Alert threshold in SDs above baseline (default: 3.0)")
    parser.add_argument("--consecutive", type=int,   default=1,
                        help="Weeks above threshold to declare alert (1=single, 3=EARS-C3)")
    args = parser.parse_args()

    print(f"=== CUSUM/EARS Anomaly Detection ===")
    print(f"  Evaluation year: {args.year} | k={args.k} | consecutive={args.consecutive}")

    conn = get_conn()
    df = load_surveillance(conn, args.year)
    conn.close()

    print(f"  Rows: {len(df)} | Target positive: {int(df['target_outbreak_4w'].sum())}")

    # Compute CUSUM per state
    results = []
    for state_id, grp in df.groupby("state_id"):
        grp = grp.sort_values(["epi_year", "epi_week"]).copy()
        grp["z_score"] = compute_cusum(grp["cases_t1"], k=args.k)
        grp["alert"]   = cusum_alert(grp["z_score"], args.k, args.consecutive)
        results.append(grp)

    df_all = pd.concat(results).sort_values(["epi_year", "epi_week", "state_id"])
    df_all["cusum_alert_score"] = df_all["z_score"]

    # Evaluate on test year only
    test_df = df_all[df_all["epi_year"] == args.year].copy()
    test_df["alert_score"] = test_df["z_score"]

    # Rename for evaluate function
    test_df["cusum_score"] = test_df["z_score"]
    # Add a column evaluate_cusum expects
    test_df["cusum_score_score"] = test_df["z_score"]

    metrics = evaluate_cusum(
        test_df.assign(**{"alert_score": test_df["z_score"]}),
        alert_col="alert",
        target_col="target_outbreak_4w",
    )
    # Fix: add the score column the evaluator looks for
    test_df["alert_score"] = test_df["z_score"]
    metrics = {
        "year":          args.year,
        "k":             args.k,
        "consecutive":   args.consecutive,
        "n":             len(test_df),
        "n_pos":         int(test_df["target_outbreak_4w"].sum()),
    }

    y_true = test_df["target_outbreak_4w"].values.astype(int)
    y_pred = test_df["alert"].values.astype(int)
    z_vals = test_df["z_score"].values

    roc_auc = roc_auc_score(y_true, z_vals) if y_true.sum() > 0 else None
    pr_auc  = average_precision_score(y_true, z_vals) if y_true.sum() > 0 else None
    cm = confusion_matrix(y_true, y_pred)
    if cm.shape == (2, 2):
        tn, fp, fn, tp = cm.ravel()
    else:
        tn, fp, fn, tp = int(cm[0, 0]), 0, 0, 0
    sens = tp / (tp + fn) if (tp + fn) > 0 else 0
    spec = tn / (tn + fp) if (tn + fp) > 0 else 0
    far  = fp / (fp + tn) if (fp + tn) > 0 else 0

    metrics.update({
        "roc_auc": round(roc_auc, 4) if roc_auc else None,
        "pr_auc":  round(pr_auc, 4)  if pr_auc  else None,
        "sensitivity": round(sens, 4),
        "specificity": round(spec, 4),
        "false_alarm_rate": round(far, 4),
        "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
    })

    print(f"\n  Test {args.year}: n={metrics['n']} | pos={metrics['n_pos']}")
    print(f"  ROC-AUC={metrics['roc_auc']} | PR-AUC={metrics['pr_auc']}")
    print(f"  Sens={metrics['sensitivity']} | Spec={metrics['specificity']} | "
          f"FAR={metrics['false_alarm_rate']}")
    print(f"  TP={tp} FP={fp} FN={fn} TN={tn}")

    # Alert distribution by state in test year
    alert_states = test_df[test_df["alert"] == 1]["state_id"].value_counts()
    print(f"\n  States alerting in {args.year}: {len(alert_states)}")

    # Save outputs
    os.makedirs("p4_early_warning/models", exist_ok=True)
    out_path = f"p4_early_warning/models/cusum_alerts_{args.year}.csv"
    test_df[["state_id", "epi_year", "epi_week", "cases_t1", "z_score",
             "alert", "target_outbreak_4w"]].to_csv(out_path, index=False)
    print(f"\n  Alerts saved → {out_path}")

    metrics_path = f"p4_early_warning/models/cusum_metrics_{args.year}.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"  Metrics saved → {metrics_path}")


if __name__ == "__main__":
    main()
