"""
P4 Task 01 — XGBoost Early Warning Model
Trains a binary classifier to predict mpox outbreak 4 weeks ahead.
Train: 2017–2022 | Val: 2023 | Test: 2024

Run: python p4_early_warning/train_model.py [--save-model] [--tune]

Outputs:
  - Model artefact (models/xgb_outbreak_v1.json)
  - Metrics summary (models/metrics_v1.json)
  - Feature importances (models/feature_importance_v1.csv)
"""
import os, json, argparse
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from datetime import datetime, timezone
from sklearn.metrics import (roc_auc_score, average_precision_score,
                              confusion_matrix, classification_report,
                              precision_recall_curve)
from sklearn.preprocessing import StandardScaler
import xgboost as xgb

load_dotenv()

# ── Feature columns used in the model ─────────────────────────────────────────
FEATURE_COLS = [
    # Surveillance lags
    "cases_t1", "cases_t2", "cases_t4",
    "cases_rolling4w_mean", "cases_rolling8w_mean", "cases_log1p",
    # Derived: velocity and acceleration
    "cases_velocity",     # cases_t1 - cases_t2 (1-week rate of change)
    "cases_accel",        # velocity change over 3-week window
    # Climate
    "rainfall_t2_mm", "rainfall_t4_mm", "temp_mean_t1_c",
    # Reservoir/ecological
    "reservoir_risk_index",
    # Spatial
    "is_border_state", "neighbour_cases_t1",
    # Seasonality (cyclic encoding)
    "week_sin", "week_cos",
]

TARGET_COL = "target_outbreak_4w"


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def load_features(conn) -> pd.DataFrame:
    """Load complete, labeled feature rows from the warehouse."""
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
    return df


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add cyclic seasonality + case velocity/acceleration features."""
    df["week_sin"]      = np.sin(2 * np.pi * df["epi_week"] / 52)
    df["week_cos"]      = np.cos(2 * np.pi * df["epi_week"] / 52)
    df["cases_velocity"] = df["cases_t1"] - df["cases_t2"]
    df["cases_accel"]    = (df["cases_t1"] - df["cases_t2"]) - (df["cases_t2"] - df["cases_t4"]) / 2
    return df


def temporal_split(df: pd.DataFrame):
    """Strict temporal split: train 2017-2022, val 2023, test 2024."""
    train = df[df["epi_year"] <= 2022].copy()
    val   = df[df["epi_year"] == 2023].copy()
    test  = df[df["epi_year"] == 2024].copy()
    return train, val, test


def print_split_stats(name, df):
    n = len(df)
    pos = df[TARGET_COL].sum()
    print(f"  {name:6s}: {n:6d} rows | {int(pos):4d} positive ({100*pos/n:.2f}%)")


def compute_scale_pos_weight(y_train):
    """XGBoost scale_pos_weight = neg/pos for class imbalance."""
    neg = (y_train == 0).sum()
    pos = (y_train == 1).sum()
    return neg / pos if pos > 0 else 1.0


def build_model(scale_pos_weight: float, seed: int = 42) -> xgb.XGBClassifier:
    return xgb.XGBClassifier(
        n_estimators=500,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        scale_pos_weight=scale_pos_weight,
        objective="binary:logistic",
        eval_metric="aucpr",
        early_stopping_rounds=30,
        random_state=seed,
        n_jobs=-1,
        verbosity=0,
    )


def evaluate(name: str, model, X, y, threshold: float = 0.5) -> dict:
    """Full evaluation suite: AUC-ROC, AUC-PR, confusion matrix, Youden threshold."""
    probs = model.predict_proba(X)[:, 1]

    roc_auc = roc_auc_score(y, probs) if y.sum() > 0 else None
    pr_auc  = average_precision_score(y, probs) if y.sum() > 0 else None

    # Youden-optimal threshold on validation set (not applied here — passed in)
    preds = (probs >= threshold).astype(int)
    cm = confusion_matrix(y, preds)
    tn, fp, fn, tp = cm.ravel() if cm.shape == (2, 2) else (cm[0,0], 0, 0, 0)

    sensitivity = tp / (tp + fn) if (tp + fn) > 0 else 0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0
    ppv         = tp / (tp + fp) if (tp + fp) > 0 else 0
    false_alarm = fp / (fp + tn) if (fp + tn) > 0 else 0

    metrics = {
        "split":       name,
        "n":           int(len(y)),
        "n_pos":       int(y.sum()),
        "roc_auc":     round(roc_auc, 4) if roc_auc else None,
        "pr_auc":      round(pr_auc, 4)  if pr_auc  else None,
        "threshold":   round(threshold, 4),
        "sensitivity": round(sensitivity, 4),
        "specificity": round(specificity, 4),
        "ppv":         round(ppv, 4),
        "false_alarm_rate": round(false_alarm, 4),
        "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
    }

    roc_str = f"{roc_auc:.4f}" if roc_auc is not None else "N/A"
    pr_str  = f"{pr_auc:.4f}"  if pr_auc  is not None else "N/A"
    print(f"\n  [{name}] n={len(y)} | pos={int(y.sum())} | "
          f"ROC-AUC={roc_str} | PR-AUC={pr_str}")
    print(f"    Sens={sensitivity:.3f}  Spec={specificity:.3f}  "
          f"PPV={ppv:.3f}  FalseAlarm={false_alarm:.3f}  thresh={threshold:.3f}")
    print(f"    CM: TP={tp} FP={fp} FN={fn} TN={tn}")
    return metrics


def find_youden_threshold(model, X_val, y_val) -> float:
    """Maximise Youden J (sensitivity + specificity - 1) on the validation set."""
    probs = model.predict_proba(X_val)[:, 1]
    precision, recall, thresholds = precision_recall_curve(y_val, probs)
    # We use sensitivity (recall) + specificity approach via ROC
    from sklearn.metrics import roc_curve
    fpr, tpr, roc_thresh = roc_curve(y_val, probs)
    j_scores = tpr - fpr
    best_idx = np.argmax(j_scores)
    return float(roc_thresh[best_idx])


def save_outputs(model, metrics_list, df_importance, args):
    os.makedirs("p4_early_warning/models", exist_ok=True)
    if args.save_model:
        model.save_model("p4_early_warning/models/xgb_outbreak_v1.json")
        print("\n  Model saved → p4_early_warning/models/xgb_outbreak_v1.json")

    metrics_path = "p4_early_warning/models/metrics_v1.json"
    with open(metrics_path, "w") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "model": "XGBClassifier",
            "target": "target_outbreak_4w (4-week horizon)",
            "train_period": "2017-2022",
            "val_period":   "2023",
            "test_period":  "2024",
            "splits": metrics_list,
        }, f, indent=2)
    print(f"  Metrics saved → {metrics_path}")

    importance_path = "p4_early_warning/models/feature_importance_v1.csv"
    df_importance.to_csv(importance_path, index=False)
    print(f"  Importances → {importance_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-model", action="store_true")
    parser.add_argument("--tune",       action="store_true", help="Optuna hyperparameter search")
    args = parser.parse_args()

    print("=== SmartMpox XGBoost Early Warning Model ===")
    print(f"  Features: {len(FEATURE_COLS)} | Target: {TARGET_COL}")

    conn = get_conn()
    df = load_features(conn)
    conn.close()
    print(f"  Loaded: {len(df)} labeled rows")

    df = add_derived_features(df)

    train_df, val_df, test_df = temporal_split(df)
    print("\nSplit summary:")
    print_split_stats("train", train_df)
    print_split_stats("val",   val_df)
    print_split_stats("test",  test_df)

    X_train = train_df[FEATURE_COLS].values.astype(np.float32)
    y_train = train_df[TARGET_COL].values.astype(int)
    X_val   = val_df[FEATURE_COLS].values.astype(np.float32)
    y_val   = val_df[TARGET_COL].values.astype(int)
    X_test  = test_df[FEATURE_COLS].values.astype(np.float32)
    y_test  = test_df[TARGET_COL].values.astype(int)

    spw = compute_scale_pos_weight(y_train)
    print(f"\n  scale_pos_weight = {spw:.2f}")

    model = build_model(spw)
    print("\nTraining ...")
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )
    print(f"  Best iteration: {model.best_iteration}")

    # Youden threshold on validation
    threshold = find_youden_threshold(model, X_val, y_val) if y_val.sum() > 0 else 0.5
    print(f"  Youden threshold (val): {threshold:.4f}")

    print("\nEvaluation:")
    metrics_list = [
        evaluate("train", model, X_train, y_train, threshold),
        evaluate("val",   model, X_val,   y_val,   threshold),
        evaluate("test",  model, X_test,  y_test,  threshold),
    ]

    # Feature importances
    importances = model.feature_importances_
    df_importance = pd.DataFrame({
        "feature": FEATURE_COLS,
        "gain":    importances,
    }).sort_values("gain", ascending=False)
    print("\nTop feature importances (gain):")
    for _, row in df_importance.head(10).iterrows():
        print(f"  {row['feature']:30s} {row['gain']:.4f}")

    save_outputs(model, metrics_list, df_importance, args)

    # KPI gate check
    test_metrics = next(m for m in metrics_list if m["split"] == "test")
    print("\n=== KPI-5 Gate Check ===")
    kpis = {
        "AUC-ROC ≥ 0.80":       test_metrics["roc_auc"] is not None and test_metrics["roc_auc"] >= 0.80,
        "False alarm ≤ 20%":    test_metrics["false_alarm_rate"] <= 0.20,
        "Sensitivity ≥ 0.70":   test_metrics["sensitivity"] >= 0.70,
    }
    for label, passed in kpis.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")


if __name__ == "__main__":
    main()
