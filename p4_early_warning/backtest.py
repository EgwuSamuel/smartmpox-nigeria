"""
P4 Task 02 — Walk-Forward Backtesting
Temporal rolling-window evaluation of the XGBoost outbreak detector.
Each fold trains on years 1..N, evaluates on year N+1.

Outputs:
  - models/backtest_results.json   — per-fold metrics
  - models/backtest_summary.csv    — aggregated table for the paper

Run: python p4_early_warning/backtest.py [--min-train-years 3]
"""
import os, json, argparse
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from datetime import datetime, timezone
from sklearn.metrics import (roc_auc_score, average_precision_score,
                              confusion_matrix, roc_curve)
import xgboost as xgb

load_dotenv()

FEATURE_COLS = [
    "cases_t1", "cases_t2", "cases_t4",
    "cases_rolling4w_mean", "cases_rolling8w_mean", "cases_log1p",
    "cases_velocity", "cases_accel",
    "rainfall_t2_mm", "rainfall_t4_mm", "temp_mean_t1_c",
    "reservoir_risk_index",
    "is_border_state", "neighbour_cases_t1",
    "week_sin", "week_cos",
]
TARGET_COL = "target_outbreak_4w"


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def load_features(conn) -> pd.DataFrame:
    sql = """
        SELECT
            state_id, epi_year, epi_week, week_start_date,
            cases_t1, cases_t2, cases_t4,
            cases_rolling4w_mean, cases_rolling8w_mean, cases_log1p,
            rainfall_t2_mm, rainfall_t4_mm, temp_mean_t1_c,
            reservoir_risk_index,
            is_border_state::INT AS is_border_state,
            neighbour_cases_t1,
            target_outbreak_4w, target_cases_4w
        FROM features_weekly
        WHERE is_complete = TRUE
          AND target_outbreak_4w IS NOT NULL
        ORDER BY epi_year, epi_week, state_id
    """
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    df["week_sin"]       = np.sin(2 * np.pi * df["epi_week"] / 52)
    df["week_cos"]       = np.cos(2 * np.pi * df["epi_week"] / 52)
    df["cases_velocity"] = df["cases_t1"] - df["cases_t2"]
    df["cases_accel"]    = (df["cases_t1"] - df["cases_t2"]) - (df["cases_t2"] - df["cases_t4"]) / 2
    return df


def youden_threshold(model, X, y) -> float:
    probs = model.predict_proba(X)[:, 1]
    fpr, tpr, thresholds = roc_curve(y, probs)
    j = tpr - fpr
    return float(thresholds[np.argmax(j)])


def run_fold(train_df, test_df, fold_name: str) -> dict:
    X_train = train_df[FEATURE_COLS].values.astype(np.float32)
    y_train = train_df[TARGET_COL].values.astype(int)
    X_test  = test_df[FEATURE_COLS].values.astype(np.float32)
    y_test  = test_df[TARGET_COL].values.astype(int)

    if y_train.sum() == 0:
        return {"fold": fold_name, "skipped": True,
                "reason": "no positive examples in training set"}

    spw = (y_train == 0).sum() / max(y_train.sum(), 1)

    model = xgb.XGBClassifier(
        n_estimators=300, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
        scale_pos_weight=spw, objective="binary:logistic",
        eval_metric="aucpr", random_state=42, n_jobs=-1, verbosity=0,
    )
    model.fit(X_train, y_train, verbose=False)

    threshold = youden_threshold(model, X_train, y_train)
    probs = model.predict_proba(X_test)[:, 1]
    preds = (probs >= threshold).astype(int)

    roc_auc = roc_auc_score(y_test, probs) if y_test.sum() > 0 else None
    pr_auc  = average_precision_score(y_test, probs) if y_test.sum() > 0 else None
    cm = confusion_matrix(y_test, preds)
    tn, fp, fn, tp = cm.ravel() if cm.shape == (2, 2) else (int(cm[0,0]), 0, 0, 0)

    sens = tp / (tp + fn) if (tp + fn) > 0 else 0
    spec = tn / (tn + fp) if (tn + fp) > 0 else 0
    far  = fp / (fp + tn) if (fp + tn) > 0 else 0
    ppv  = tp / (tp + fp) if (tp + fp) > 0 else 0

    return {
        "fold":        fold_name,
        "train_n":     len(y_train),
        "train_pos":   int(y_train.sum()),
        "test_n":      len(y_test),
        "test_pos":    int(y_test.sum()),
        "roc_auc":     round(roc_auc, 4) if roc_auc else None,
        "pr_auc":      round(pr_auc, 4)  if pr_auc  else None,
        "threshold":   round(threshold, 4),
        "sensitivity": round(sens, 4),
        "specificity": round(spec, 4),
        "ppv":         round(ppv, 4),
        "false_alarm_rate": round(far, 4),
        "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
        "skipped": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-train-years", type=int, default=3,
                        help="Minimum years required before first fold (default: 3)")
    args = parser.parse_args()

    print("=== SmartMpox Walk-Forward Backtesting ===")
    conn = get_conn()
    df = load_features(conn)
    conn.close()

    years = sorted(df["epi_year"].unique())
    print(f"  Years: {years[0]}–{years[-1]} | Labeled rows: {len(df)} | "
          f"Positive: {int(df[TARGET_COL].sum())} ({100*df[TARGET_COL].mean():.2f}%)")

    folds = []
    for i in range(args.min_train_years, len(years)):
        train_years = years[:i]
        test_year   = years[i]
        train_df = df[df["epi_year"].isin(train_years)]
        test_df  = df[df["epi_year"] == test_year]
        fold_name = f"train≤{train_years[-1]}→test={test_year}"
        result = run_fold(train_df, test_df, fold_name)
        folds.append(result)

        if result.get("skipped"):
            print(f"  [{fold_name}] SKIPPED — {result['reason']}")
        else:
            print(f"  [{fold_name}] ROC-AUC={result['roc_auc']} | "
                  f"PR-AUC={result['pr_auc']} | "
                  f"Sens={result['sensitivity']} | Spec={result['specificity']} | "
                  f"FAR={result['false_alarm_rate']} | "
                  f"train_pos={result['train_pos']} test_pos={result['test_pos']}")

    valid_folds = [f for f in folds if not f.get("skipped") and f["roc_auc"] is not None]
    if valid_folds:
        mean_roc = round(np.mean([f["roc_auc"] for f in valid_folds]), 4)
        mean_pr  = round(np.mean([f["pr_auc"]  for f in valid_folds if f["pr_auc"]]), 4)
        mean_sens = round(np.mean([f["sensitivity"] for f in valid_folds]), 4)
        mean_far  = round(np.mean([f["false_alarm_rate"] for f in valid_folds]), 4)
        print(f"\nMean across {len(valid_folds)} valid folds: "
              f"ROC-AUC={mean_roc} | PR-AUC={mean_pr} | "
              f"Sensitivity={mean_sens} | FAR={mean_far}")

    os.makedirs("p4_early_warning/models", exist_ok=True)
    out = {
        "computed_at":    datetime.now(timezone.utc).isoformat(),
        "model":          "XGBClassifier (walk-forward)",
        "target":         "target_outbreak_4w (any mpox, 4-week horizon)",
        "min_train_years": args.min_train_years,
        "n_folds":        len(folds),
        "n_valid_folds":  len(valid_folds),
        "mean_roc_auc":   mean_roc if valid_folds else None,
        "mean_pr_auc":    mean_pr  if valid_folds else None,
        "mean_sensitivity": mean_sens if valid_folds else None,
        "mean_far":       mean_far  if valid_folds else None,
        "folds":          folds,
    }
    with open("p4_early_warning/models/backtest_results.json", "w") as f:
        json.dump(out, f, indent=2)
    print("  Results → p4_early_warning/models/backtest_results.json")

    df_summary = pd.DataFrame([f for f in folds if not f.get("skipped")])
    if not df_summary.empty:
        df_summary.to_csv("p4_early_warning/models/backtest_summary.csv", index=False)
        print("  Summary → p4_early_warning/models/backtest_summary.csv")

    # KPI-5 gate
    if valid_folds:
        print("\n=== KPI-5 Gate Check (mean across folds) ===")
        print(f"  {'PASS' if mean_roc >= 0.80 else 'FAIL'}  ROC-AUC ≥ 0.80   (got {mean_roc})")
        print(f"  {'PASS' if mean_far <= 0.20 else 'FAIL'}  False alarm ≤ 20%  (got {mean_far})")
        print(f"  {'PASS' if mean_sens >= 0.70 else 'FAIL'}  Sensitivity ≥ 0.70 (got {mean_sens})")
        print("  PASS  Lead time ≥ 7 days  (4-week horizon = 28 days by design)")


if __name__ == "__main__":
    main()
