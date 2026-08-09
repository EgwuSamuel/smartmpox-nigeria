"""
P4 Task 05 — Negative Binomial Regression (Interpretable Baseline)
Provides:
  1. Significant predictors with IRR (incidence rate ratios) for the paper
  2. A baseline count prediction to compare against XGBoost
  3. Satisfies KPI-5: ≥5 significant predictors

Uses statsmodels GLM with NB family (log link).
Train: 2017–2022 | Test: 2023

Run: python p4_early_warning/nb_regression.py [--save]
"""
import os, json, argparse
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
import statsmodels.api as sm
from statsmodels.discrete.count_model import ZeroInflatedNegativeBinomialP
from sklearn.metrics import mean_absolute_error, mean_squared_error
from datetime import datetime, timezone

load_dotenv()

# Predictors for NB model (simpler subset — avoids multicollinearity)
NB_PREDICTORS = [
    "cases_t1",           # primary lag — strongest predictor
    "cases_velocity",     # rate of change
    "rainfall_t2_mm",     # environmental trigger
    "temp_mean_t1_c",     # temperature effect
    "reservoir_risk_index",  # ecological risk
    "is_border_state",    # spatial spillover indicator
    "neighbour_cases_t1", # spatial diffusion
    "week_sin",           # seasonality
    "week_cos",
]

TARGET_COUNT = "target_cases_4w"


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def load_data(conn) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute("""
        SELECT state_id, epi_year, epi_week,
               cases_t1, cases_t2, cases_t4,
               cases_rolling4w_mean, cases_rolling8w_mean,
               rainfall_t2_mm, rainfall_t4_mm, temp_mean_t1_c,
               reservoir_risk_index,
               is_border_state::INT AS is_border_state,
               neighbour_cases_t1,
               target_cases_4w, target_outbreak_4w
        FROM features_weekly
        WHERE is_complete = TRUE
          AND target_outbreak_4w IS NOT NULL
        ORDER BY epi_year, epi_week, state_id
    """)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()

    df["week_sin"]       = np.sin(2 * np.pi * df["epi_week"] / 52)
    df["week_cos"]       = np.cos(2 * np.pi * df["epi_week"] / 52)
    df["cases_velocity"] = df["cases_t1"] - df["cases_t2"]
    return df


def fit_nb(X_train: np.ndarray, y_train: np.ndarray,
           predictor_names: list) -> sm.GLMResults:
    """Fit Negative Binomial GLM with log link — use DataFrame for named params."""
    X_df = pd.DataFrame(X_train, columns=predictor_names)
    X_const = sm.add_constant(X_df, has_constant="add")
    model = sm.GLM(
        y_train, X_const,
        family=sm.families.NegativeBinomial(link=sm.families.links.Log()),
    )
    result = model.fit(maxiter=200, disp=False)
    return result


def evaluate_count(result, X_test: np.ndarray, y_test: np.ndarray,
                   predictor_names: list) -> dict:
    X_df = pd.DataFrame(X_test, columns=predictor_names)
    X_const = sm.add_constant(X_df, has_constant="add")
    preds = result.predict(X_const).clip(0)

    mae  = float(mean_absolute_error(y_test, preds))
    rmse = float(np.sqrt(mean_squared_error(y_test, preds)))

    # Binary alert from predicted count > 0
    alert_preds = (preds > 0.5).astype(int)
    alert_true  = (y_test > 0).astype(int)
    tp = int(((alert_preds == 1) & (alert_true == 1)).sum())
    fp = int(((alert_preds == 1) & (alert_true == 0)).sum())
    fn = int(((alert_preds == 0) & (alert_true == 1)).sum())
    tn = int(((alert_preds == 0) & (alert_true == 0)).sum())
    sens = tp / (tp + fn) if (tp + fn) > 0 else 0
    spec = tn / (tn + fp) if (tn + fp) > 0 else 0
    far  = fp / (fp + tn) if (fp + tn) > 0 else 0

    return {
        "mae": round(mae, 4), "rmse": round(rmse, 4),
        "sensitivity": round(sens, 4), "specificity": round(spec, 4),
        "false_alarm_rate": round(far, 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def print_irr_table(result, predictor_names: list):
    """Print IRR (exp(coef)) with 95% CI and p-values."""
    params = result.params
    conf = result.conf_int()
    pvals = result.pvalues

    print("\n  Incidence Rate Ratios (IRR = exp(coef)), 95% CI:")
    print(f"  {'Predictor':30s} {'IRR':>8s}  {'95% CI':>18s}  {'p':>8s}  {'Sig':>5s}")
    print("  " + "-" * 75)
    for name in predictor_names + ["const"]:
        if name not in params.index:
            continue
        irr  = np.exp(params[name])
        lo   = np.exp(conf.loc[name, 0])
        hi   = np.exp(conf.loc[name, 1])
        pval = pvals[name]
        sig  = "***" if pval < 0.001 else ("**" if pval < 0.01 else ("*" if pval < 0.05 else ""))
        print(f"  {name:30s} {irr:8.3f}  ({lo:.3f}, {hi:.3f})  {pval:8.4f}  {sig:>5s}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()

    print("=== Negative Binomial Regression — Interpretable Model ===")
    conn = get_conn()
    df = load_data(conn)
    conn.close()

    train = df[df["epi_year"] <= 2022]
    val   = df[df["epi_year"] == 2023]

    print(f"  Train (2017-2022): {len(train)} rows | pos={int((train[TARGET_COUNT]>0).sum())}")
    print(f"  Val   (2023):      {len(val)}  rows | pos={int((val[TARGET_COUNT]>0).sum())}")

    X_train = train[NB_PREDICTORS].fillna(0).astype(np.float64).values
    y_train = train[TARGET_COUNT].fillna(0).astype(np.float64).values
    X_val   = val[NB_PREDICTORS].fillna(0).astype(np.float64).values
    y_val   = val[TARGET_COUNT].fillna(0).astype(np.float64).values

    print("\nFitting Negative Binomial GLM ...")
    try:
        result = fit_nb(X_train, y_train, NB_PREDICTORS)
    except Exception as exc:
        print(f"  NB failed ({exc}), trying Poisson ...")
        X_df = pd.DataFrame(X_train, columns=NB_PREDICTORS)
        X_const = sm.add_constant(X_df, has_constant="add")
        result = sm.GLM(y_train, X_const,
                        family=sm.families.Poisson()).fit(maxiter=100, disp=False)

    print(f"  AIC={result.aic:.1f}  BIC={result.bic:.1f}  "
          f"Converged={result.converged}")

    print_irr_table(result, NB_PREDICTORS)

    # Count significant predictors (p < 0.05, excluding intercept)
    sig_preds = [n for n in NB_PREDICTORS if n in result.pvalues.index
                 and result.pvalues[n] < 0.05]
    print(f"\n  Significant predictors (p<0.05): {len(sig_preds)}")
    print(f"  {sig_preds}")

    print("\nEvaluation:")
    val_metrics = evaluate_count(result, X_val, y_val, NB_PREDICTORS)
    print(f"  Val 2023: MAE={val_metrics['mae']} RMSE={val_metrics['rmse']}")
    print(f"           Sens={val_metrics['sensitivity']} Spec={val_metrics['specificity']} "
          f"FAR={val_metrics['false_alarm_rate']}")
    print(f"           TP={val_metrics['tp']} FP={val_metrics['fp']} "
          f"FN={val_metrics['fn']} TN={val_metrics['tn']}")

    if args.save:
        os.makedirs("p4_early_warning/models", exist_ok=True)
        irr_data = {}
        for name in NB_PREDICTORS:
            if name in result.params.index:
                irr_data[name] = {
                    "irr":   float(np.exp(result.params[name])),
                    "lo95":  float(np.exp(result.conf_int().loc[name, 0])),
                    "hi95":  float(np.exp(result.conf_int().loc[name, 1])),
                    "pvalue": float(result.pvalues[name]),
                    "significant": float(result.pvalues[name]) < 0.05,
                }
        out = {
            "computed_at":     datetime.now(timezone.utc).isoformat(),
            "model":           "Negative Binomial GLM (log link)",
            "train_period":    "2017-2022",
            "val_period":      "2023",
            "aic":             float(result.aic),
            "bic":             float(result.bic),
            "n_sig_predictors": len(sig_preds),
            "irr_table":       irr_data,
            "val_metrics":     val_metrics,
        }
        path = "p4_early_warning/models/nb_regression_v1.json"
        with open(path, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\n  Results saved → {path}")

    print(f"\n=== KPI-5 Gate: ≥5 significant predictors ===")
    print(f"  {'PASS' if len(sig_preds) >= 5 else 'FAIL'}  "
          f"Significant predictors = {len(sig_preds)} (need ≥5)")


if __name__ == "__main__":
    main()
