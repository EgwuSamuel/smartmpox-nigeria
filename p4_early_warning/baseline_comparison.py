"""
P4 Task 08 — System-Level Baseline Comparison
=============================================
Benchmarks three COMPLETE outbreak-warning systems on identical temporal folds
and an identical 4-week-ahead target (target_outbreak_4w), to show that the
knowledge-based multimodal system outperforms what health ministries currently
deploy.

  System 1  ARIMA + threshold      — current practice; case counts only (1 stream)
  System 2  Negative-Binomial GLM  — statistical baseline; 4 streams
  System 3  SmartMpox (XGBoost +   — hybrid ML + knowledge-based expert layer;
            expert inference layer)   5 streams (adds digital surveillance)

Every system is scored on the SAME test rows with AUC / Sensitivity / FAR / PPV,
walk-forward and on the 2024 Clade-I stress test. No leakage: each system is
fitted only on data preceding its test year.

Run:  python p4_early_warning/baseline_comparison.py
Out:  p4_early_warning/models/baseline_comparison_results.json
"""
import os, json, warnings
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from datetime import datetime, timezone
from sklearn.metrics import roc_auc_score

import statsmodels.api as sm
from statsmodels.tsa.arima.model import ARIMA
from sklearn.metrics import roc_curve

# Reuse the expert engine + XGBoost evidence model (no leakage, fitted per fold)
from expert_system import (
    FEATURE_COLS, TARGET, load_features, build_engine, alert_metrics,
)


def sensitivity_at_far(y_binary, scores, far_budget=0.10):
    """
    Threshold-INDEPENDENT operating comparison: the best sensitivity each system
    can reach without exceeding a fixed false-alarm budget. This is the fair way
    to compare systems whose Youden operating points sit at different FARs.
    """
    y = np.asarray(y_binary).astype(int)
    s = np.asarray(scores, dtype=float)
    if y.sum() == 0 or len(np.unique(s)) < 2:
        return None
    fpr, tpr, _ = roc_curve(y, s)
    ok = fpr <= far_budget
    return round(float(np.max(tpr[ok])), 4) if ok.any() else 0.0


def youden_threshold(y_binary, scores) -> float:
    """Youden-J optimal cutoff on TRAINING scores (fair, leakage-free operating point)."""
    y = np.asarray(y_binary).astype(int)
    s = np.asarray(scores, dtype=float)
    if y.sum() == 0 or len(np.unique(s)) < 2:
        return float(np.max(s)) + 1e-9   # never alerts if train has no signal
    fpr, tpr, thr = roc_curve(y, s)
    t = float(thr[np.argmax(tpr - fpr)])
    return t if np.isfinite(t) else float(np.median(s))

load_dotenv()
warnings.filterwarnings("ignore")   # silence ARIMA convergence chatter

# NB GLM predictors (cases + climate + reservoir + spatial + seasonality) = 4 streams
NB_PREDICTORS = [
    "cases_t1", "cases_velocity",
    "rainfall_t2_mm", "temp_mean_t1_c",
    "reservoir_risk_index",
    "is_border_state", "neighbour_cases_t1",
    "week_sin", "week_cos",
]

FOLDS = [(2019, 2020), (2020, 2021), (2021, 2022), (2022, 2023)]
CLADE_FOLD = (2023, 2024)   # headline cross-clade stress test


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


# ─────────────────────────── System 1: ARIMA ─────────────────────────────────
def load_case_series() -> pd.DataFrame:
    """
    Continuous weekly case series per state. surveillance_weekly only stores
    reporting weeks (~522 rows), so we take the full state-week grid from
    features_weekly and LEFT JOIN actual cases, filling implicit zeros — the
    honest input an ARIMA surveillance model would actually see.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT f.state_id, f.epi_year, f.epi_week,
               COALESCE(s.total_cases, 0) AS cases
        FROM features_weekly f
        LEFT JOIN surveillance_weekly s
          ON s.state_id = f.state_id AND s.epi_year = f.epi_year AND s.epi_week = f.epi_week
        ORDER BY f.state_id, f.epi_year, f.epi_week
    """)
    df = pd.DataFrame(cur.fetchall(), columns=["state_id", "epi_year", "epi_week", "cases"])
    cur.close(); conn.close()
    return df


def _fit_best_arima(history: np.ndarray):
    """Small AIC grid with a naive-mean fallback for degenerate sparse series."""
    best, best_aic = None, np.inf
    for order in [(1, 0, 0), (0, 1, 1), (1, 1, 1)]:
        try:
            res = ARIMA(history, order=order).fit()
            if np.isfinite(res.aic) and res.aic < best_aic:
                best, best_aic = res, res.aic
        except Exception:
            continue
    return best


def arima_scores_for_fold(series_df, train_max, test_year) -> dict:
    """
    Rolling-origin 4-week-ahead forecast per state through the test year.
    Returns {(state_id, epi_year, epi_week): expected_cases_next_4w}.
    Decision at week t uses observations up to and including t (no look-ahead).
    """
    scores = {}
    for state_id, g in series_df.groupby("state_id"):
        g = g.sort_values(["epi_year", "epi_week"]).reset_index(drop=True)
        hist = g[g["epi_year"] <= train_max]["cases"].astype(float).values
        test_rows = g[g["epi_year"] == test_year]
        if len(test_rows) == 0:
            continue
        if len(hist) < 8 or np.all(hist == 0):
            # not enough signal to fit; forecast = historical mean
            mean_fc = float(hist.mean()) if len(hist) else 0.0
            for _, r in test_rows.iterrows():
                scores[(state_id, int(r.epi_year), int(r.epi_week))] = mean_fc * 4
            continue

        res = _fit_best_arima(hist)
        if res is None:
            mean_fc = float(hist.mean())
            for _, r in test_rows.iterrows():
                scores[(state_id, int(r.epi_year), int(r.epi_week))] = mean_fc * 4
            continue

        # roll through the test year
        for _, r in test_rows.iterrows():
            try:
                res = res.append([float(r.cases)], refit=False)
                fc = res.forecast(steps=4)
                score = float(np.clip(np.asarray(fc), 0, None).sum())
            except Exception:
                score = float(hist.mean() * 4)
            scores[(state_id, int(r.epi_year), int(r.epi_week))] = score
    return scores


def _scores_for_rows(df_rows, score_map):
    keys = list(zip(df_rows["state_id"], df_rows["epi_year"], df_rows["epi_week"]))
    return np.array([score_map.get((int(s), int(y), int(w)), 0.0) for (s, y, w) in keys])


def eval_arima(test_df, test_scores, threshold) -> dict:
    score = _scores_for_rows(test_df, test_scores)
    y = test_df[TARGET].values.astype(int)
    auc = roc_auc_score(y, score) if y.sum() and len(np.unique(score)) > 1 else None
    alert = (score >= threshold).astype(int)
    m = alert_metrics(y, alert)
    m["auc"] = round(auc, 4) if auc is not None else None
    m["sens_at_far10"] = sensitivity_at_far(y, score, 0.10)
    return m


# ─────────────────────────── System 2: NB GLM ────────────────────────────────
def eval_nb_glm(train_df, test_df) -> dict:
    Xtr = train_df[NB_PREDICTORS].fillna(0).astype(np.float64).values
    ytr = train_df["target_cases_4w"].fillna(0).astype(np.float64).values
    Xte = test_df[NB_PREDICTORS].fillna(0).astype(np.float64).values
    y   = test_df[TARGET].values.astype(int)

    Xtr_df = sm.add_constant(pd.DataFrame(Xtr, columns=NB_PREDICTORS), has_constant="add")
    Xte_df = sm.add_constant(pd.DataFrame(Xte, columns=NB_PREDICTORS), has_constant="add")
    try:
        res = sm.GLM(ytr, Xtr_df,
                     family=sm.families.NegativeBinomial(link=sm.families.links.Log())
                     ).fit(maxiter=200, disp=False)
    except Exception:
        res = sm.GLM(ytr, Xtr_df, family=sm.families.Poisson()).fit(maxiter=100, disp=False)

    # Fair operating point: Youden-J on TRAIN predictions (no leakage)
    train_score = np.asarray(res.predict(Xtr_df)).clip(0)
    thr = youden_threshold((ytr > 0).astype(int), train_score)

    score = np.asarray(res.predict(Xte_df)).clip(0)
    auc = roc_auc_score(y, score) if y.sum() and len(np.unique(score)) > 1 else None
    alert = (score >= thr).astype(int)
    m = alert_metrics(y, alert)
    m["auc"] = round(auc, 4) if auc is not None else None
    m["sens_at_far10"] = sensitivity_at_far(y, score, 0.10)
    return m


# ────────────────────── System 3: SmartMpox (XGB + expert) ────────────────────
def eval_smartmpox(train_df, test_df, train_max_year) -> dict:
    model, engine, _meta = build_engine(train_df, train_max_year)
    Xte  = test_df[FEATURE_COLS].fillna(0).values.astype(np.float32)
    probs = model.predict_proba(Xte)[:, 1]
    dec   = engine.infer_frame(test_df, probs)
    y = test_df[TARGET].values.astype(int)
    auc = roc_auc_score(y, probs) if y.sum() else None
    m = alert_metrics(y, dec["expert_alert"])
    m["auc"] = round(auc, 4) if auc is not None else None
    m["sens_at_far10"] = sensitivity_at_far(y, probs, 0.10)
    return m


# ────────────────────────────── driver ───────────────────────────────────────
def run_fold(df, series_df, train_max, test_year, label):
    tr = df[df["epi_year"] <= train_max]
    te = df[df["epi_year"] == test_year]
    if len(te) == 0 or te[TARGET].sum() == 0:
        return None
    print(f"\n  Fold {label} (train<={train_max}, test={test_year}, "
          f"pos={int(te[TARGET].sum())}/{len(te)})")

    # ARIMA: test-year scores + a fair Youden threshold from rolling scores on the
    # last training year (fit on <=train_max-1) — same principle as the other systems.
    arima_test  = arima_scores_for_fold(series_df, train_max, test_year)
    arima_train = arima_scores_for_fold(series_df, train_max - 1, train_max)
    tr_year = df[df["epi_year"] == train_max]
    thr = youden_threshold(tr_year[TARGET].values.astype(int),
                           _scores_for_rows(tr_year, arima_train))
    r_arima = eval_arima(te, arima_test, thr)
    r_nb    = eval_nb_glm(tr, te)
    r_smart = eval_smartmpox(tr, te, train_max)

    for name, r in [("ARIMA+threshold", r_arima), ("NB-GLM", r_nb), ("SmartMpox", r_smart)]:
        print(f"    {name:16s} AUC={str(r['auc']):>7} Sens={r['sensitivity']:.3f} "
              f"FAR={r['false_alarm_rate']:.3f} PPV={r['ppv']:.3f}")
    return {"fold": label, "test_year": test_year, "test_positives": int(te[TARGET].sum()),
            "arima": r_arima, "nb_glm": r_nb, "smartmpox": r_smart}


def mean_metric(folds, system, key):
    vals = [f[system][key] for f in folds if f[system].get(key) is not None]
    return round(float(np.mean(vals)), 4) if vals else None


def main():
    print("=== System-Level Baseline Comparison ===")
    df = load_features()
    series_df = load_case_series()
    print(f"Features: {len(df)} state-weeks | Case series: {len(series_df)} rows")

    print("\n── Walk-forward folds ──")
    fold_results = []
    for train_max, test_year in FOLDS:
        res = run_fold(df, series_df, train_max, test_year, f"{train_max}->{test_year}")
        if res:
            fold_results.append(res)

    print("\n── Cross-clade stress test (headline) ──")
    clade = run_fold(df, series_df, CLADE_FOLD[0], CLADE_FOLD[1], f"{CLADE_FOLD[0]}->{CLADE_FOLD[1]} Clade I")

    systems = ["arima", "nb_glm", "smartmpox"]
    summary = {s: {k: mean_metric(fold_results, s, k)
                   for k in ["auc", "sensitivity", "false_alarm_rate", "ppv", "sens_at_far10"]}
               for s in systems}

    print("\n" + "=" * 90)
    print("  WALK-FORWARD MEAN — three systems  (Sens@FAR10 = sensitivity at a matched 10% false-alarm budget)")
    print(f"  {'System':22s} {'Streams':>7} {'AUC':>7} {'Sens':>7} {'FAR':>7} {'PPV':>7} {'Sens@FAR10':>10}")
    print("  " + "-" * 86)
    stream_count = {"arima": 1, "nb_glm": 4, "smartmpox": 5}
    labels = {"arima": "ARIMA+threshold", "nb_glm": "NB-GLM", "smartmpox": "SmartMpox (KB+ML)"}
    for s in systems:
        m = summary[s]
        print(f"  {labels[s]:22s} {stream_count[s]:>7} {str(m['auc']):>7} "
              f"{str(m['sensitivity']):>7} {str(m['false_alarm_rate']):>7} {str(m['ppv']):>7} "
              f"{str(m['sens_at_far10']):>10}")
    print("=" * 90)

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "description": (
            "Three complete outbreak-warning systems benchmarked on identical folds "
            "and the target_outbreak_4w target. ARIMA = current practice (cases only); "
            "NB-GLM = statistical baseline (4 streams); SmartMpox = knowledge-based "
            "hybrid (5 streams incl. digital surveillance + expert inference layer)."
        ),
        "data_streams": {"arima": "case counts (1)",
                         "nb_glm": "cases + climate + reservoir + spatial (4)",
                         "smartmpox": "cases + climate + reservoir + spatial + digital (5)"},
        "walk_forward_folds": fold_results,
        "walk_forward_summary": summary,
        "cross_clade_2024": clade,
    }
    os.makedirs("p4_early_warning/models", exist_ok=True)
    path = "p4_early_warning/models/baseline_comparison_results.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved → {path}")


if __name__ == "__main__":
    main()
