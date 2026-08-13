"""
P4 Task 07 — Knowledge-Based Expert Inference Layer
====================================================
A transparent, auditable rule-based inference engine that fuses machine-learning
evidence (XGBoost) with codified epidemiological domain knowledge (NCDC/WHO/One
Health) to produce final outbreak-alert decisions.

Design goals (for Knowledge-Based Systems / Expert Systems with Applications):
  * Explicit KNOWLEDGE BASE  — rules stored as data, each with a cited rationale.
  * Transparent INFERENCE ENGINE — every decision reports which rules fired.
  * Hybrid intelligence — ML supplies graded evidence; the knowledge base supplies
    structural priors the data are too sparse to learn (reservoir ecology, the
    border paradox, digital lead-time).
  * NO LEAKAGE — all thresholds (Youden, reservoir percentiles, digital baseline)
    are fitted on the training fold only.

The headline evaluation is clade-stratified: does the expert layer RECOVER the
outbreaks that XGBoost alone misses when Clade I emerges in 2024?

Run:  python p4_early_warning/expert_system.py
Out:  p4_early_warning/models/expert_system_results.json
"""
import os, json
import numpy as np
import pandas as pd
import psycopg2
import xgboost as xgb
from dotenv import load_dotenv
from datetime import datetime, timezone
from sklearn.metrics import roc_curve

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
TARGET = "target_outbreak_4w"


# ───────────────────────────── data loading ──────────────────────────────────
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def load_features() -> pd.DataFrame:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT state_id, epi_year, epi_week,
               cases_t1, cases_t2, cases_t4,
               cases_rolling4w_mean, cases_rolling8w_mean, cases_log1p,
               rainfall_t2_mm, rainfall_t4_mm, temp_mean_t1_c,
               reservoir_risk_index,
               is_border_state::INT AS is_border_state,
               neighbour_cases_t1,
               target_outbreak_4w, target_cases_4w
        FROM features_weekly
        WHERE is_complete = TRUE AND target_outbreak_4w IS NOT NULL
        ORDER BY epi_year, epi_week, state_id
    """)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close(); conn.close()
    return add_derived(df)


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["week_sin"]       = np.sin(2 * np.pi * df["epi_week"] / 52)
    df["week_cos"]       = np.cos(2 * np.pi * df["epi_week"] / 52)
    df["cases_velocity"] = df["cases_t1"] - df["cases_t2"]
    df["cases_accel"]    = (df["cases_t1"] - df["cases_t2"]) - (df["cases_t2"] - df["cases_t4"]) / 2
    return df


def load_national_digital_signal(train_max_year: int) -> tuple[set, float]:
    """
    Build a NATIONAL weekly mpox-chatter surge indicator from the P3 scanner.

    Rationale: only 8/343 mpox-relevant posts carry a state_id, so the digital
    signal is honestly a national-resolution early-warning corroborator (it is
    the 204-day Clade-I lead-time signal). A week is a 'surge' week if its
    mpox-relevant post count exceeds the 75th percentile of TRAINING-period
    weekly counts (threshold fitted on train only → no leakage).

    Returns (set_of_surge_(isoyear,isoweek), fitted_threshold).
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT EXTRACT(ISOYEAR FROM published_at)::int AS iy,
               EXTRACT(WEEK    FROM published_at)::int AS iw,
               COUNT(*) AS n
        FROM social_media_signals
        WHERE is_mpox_relevant = TRUE AND published_at IS NOT NULL
        GROUP BY iy, iw
        ORDER BY iy, iw
    """)
    weekly = [(int(r[0]), int(r[1]), int(r[2])) for r in cur.fetchall()]
    cur.close(); conn.close()

    train_counts = [n for (iy, iw, n) in weekly if iy <= train_max_year]
    if len(train_counts) >= 4:
        thresh = float(np.percentile(train_counts, 75))
    else:
        thresh = 1.0  # fallback: any chatter week counts if train history is thin
    thresh = max(thresh, 1.0)
    surge = {(iy, iw) for (iy, iw, n) in weekly if n >= thresh}
    return surge, thresh


# ───────────────────────────── ML evidence ───────────────────────────────────
def train_xgb(train_df: pd.DataFrame):
    """Train the XGBoost evidence model on a fold; return (model, youden_threshold)."""
    X = train_df[FEATURE_COLS].fillna(0).values.astype(np.float32)
    y = train_df[TARGET].values.astype(int)
    spw = (y == 0).sum() / max((y == 1).sum(), 1)
    model = xgb.XGBClassifier(
        n_estimators=500, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
        scale_pos_weight=spw, objective="binary:logistic",
        random_state=42, n_jobs=-1, verbosity=0,
    )
    model.fit(X, y, verbose=False)
    fpr, tpr, thr = roc_curve(y, model.predict_proba(X)[:, 1])
    youden = float(thr[np.argmax(tpr - fpr)])
    # guard against degenerate thresholds
    if not np.isfinite(youden) or youden <= 0 or youden >= 1:
        youden = 0.5
    return model, youden


# ─────────────────────── knowledge base + inference ──────────────────────────
class ExpertInferenceEngine:
    """
    Hybrid ML + knowledge-based alert engine.

    Base decision: XGBoost probability >= Youden threshold  → alert.
    The knowledge base can RESCUE borderline states the ML under-scores when
    codified domain knowledge corroborates risk. Rules are escalation-only:
    the engine may raise an alert, never silence one (fail-safe for surveillance).

    Corroboration requires a minimum ML signal (prob >= floor_frac * youden) so
    the engine never fabricates alerts for quiescent states.
    """

    # Knowledge base — each rule cites the empirical/clinical rationale.
    KNOWLEDGE_BASE = [
        {"id": "R1", "name": "reservoir_ecology",
         "rationale": "NB-GLM reservoir_risk_index is the dominant predictor "
                      "(IRR=4.49, p<0.001); high rodent-reservoir suitability is a "
                      "structural One-Health risk the sparse case data under-weight."},
        {"id": "R2", "name": "border_paradox",
         "rationale": "Border states with high reservoir suitability are structurally "
                      "exposed to cross-clade importation (Clade I entered via border "
                      "states); floor their tier so the ML cannot zero them out."},
        {"id": "R3", "name": "digital_lead_time",
         "rationale": "National mpox chatter led NCDC confirmation by 204 days at the "
                      "2024 Clade-I emergence; a digital surge corroborates latent risk "
                      "before it appears in case counts."},
        {"id": "R4", "name": "spatial_spillover",
         "rationale": "Neighbour cases in the prior week (IRR=1.022, p<0.001) signal "
                      "diffusion risk not yet realised locally."},
        {"id": "R5", "name": "case_momentum",
         "rationale": "Positive case velocity over a rising 4-week mean indicates "
                      "incipient exponential growth."},
    ]

    def __init__(self, youden, reservoir_p75, reservoir_p90, surge_weeks,
                 floor_frac=0.40, votes_required=2):
        self.youden        = youden
        self.res_p75       = reservoir_p75
        self.res_p90       = reservoir_p90
        self.surge_weeks   = surge_weeks
        self.floor_frac    = floor_frac      # min ML signal to allow rescue
        self.votes_required = votes_required  # corroborating factors needed

    def _factors(self, row) -> dict:
        """Evaluate each knowledge-base condition for one state-week."""
        iso_key = (int(row["epi_year"]), int(row["epi_week"]))
        return {
            "R1": float(row["reservoir_risk_index"]) >= self.res_p90,
            "R2": int(row["is_border_state"]) == 1 and float(row["reservoir_risk_index"]) >= self.res_p75,
            "R3": iso_key in self.surge_weeks,
            "R4": float(row["neighbour_cases_t1"]) > 0,
            "R5": float(row["cases_velocity"]) > 0 and float(row["cases_rolling4w_mean"]) > 0,
        }

    def infer(self, row, prob) -> dict:
        """Return the fused decision + provenance for one state-week."""
        base_alert = prob >= self.youden
        factors    = self._factors(row)
        n_votes    = sum(factors.values())

        rescued = False
        if not base_alert and prob >= self.floor_frac * self.youden and n_votes >= self.votes_required:
            rescued = True

        expert_alert = bool(base_alert or rescued)
        fired = [rid for rid, on in factors.items() if on] if rescued else []
        return {
            "base_alert":   bool(base_alert),
            "expert_alert": expert_alert,
            "rescued":      rescued,
            "n_votes":      int(n_votes),
            "rules_fired":  fired,
        }

    def infer_frame(self, df: pd.DataFrame, probs: np.ndarray) -> pd.DataFrame:
        out = df.copy()
        decisions = [self.infer(r, p) for (_, r), p in zip(df.iterrows(), probs)]
        for k in ["base_alert", "expert_alert", "rescued", "n_votes"]:
            out[k] = [d[k] for d in decisions]
        return out


# ───────────────────────────── evaluation ────────────────────────────────────
def alert_metrics(y_true, alert) -> dict:
    y = np.asarray(y_true).astype(int)
    a = np.asarray(alert).astype(int)
    tp = int(((a == 1) & (y == 1)).sum())
    fp = int(((a == 1) & (y == 0)).sum())
    fn = int(((a == 0) & (y == 1)).sum())
    tn = int(((a == 0) & (y == 0)).sum())
    sens = tp / (tp + fn) if (tp + fn) else 0.0
    far  = fp / (fp + tn) if (fp + tn) else 0.0
    ppv  = tp / (tp + fp) if (tp + fp) else 0.0
    return {"sensitivity": round(sens, 4), "false_alarm_rate": round(far, 4),
            "ppv": round(ppv, 4), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def build_engine(train_df, train_max_year):
    """Fit all knowledge-base thresholds on the training fold (no leakage)."""
    model, youden = train_xgb(train_df)
    res = train_df["reservoir_risk_index"].dropna().astype(float)
    res_p75 = float(np.percentile(res, 75))
    res_p90 = float(np.percentile(res, 90))
    surge, surge_thr = load_national_digital_signal(train_max_year)
    engine = ExpertInferenceEngine(youden, res_p75, res_p90, surge)
    meta = {"youden": round(youden, 4), "reservoir_p75": round(res_p75, 4),
            "reservoir_p90": round(res_p90, 4), "digital_surge_threshold": surge_thr,
            "n_surge_weeks": len(surge)}
    return model, engine, meta


def evaluate_split(train_df, test_df, train_max_year, label) -> dict:
    model, engine, meta = build_engine(train_df, train_max_year)
    X_te  = test_df[FEATURE_COLS].fillna(0).values.astype(np.float32)
    probs = model.predict_proba(X_te)[:, 1]
    dec   = engine.infer_frame(test_df, probs)

    y  = test_df[TARGET].values.astype(int)
    xgb_m    = alert_metrics(y, dec["base_alert"])
    expert_m = alert_metrics(y, dec["expert_alert"])

    rescued_pos = int(((dec["rescued"]) & (y == 1)).sum())   # outbreaks recovered
    rescued_neg = int(((dec["rescued"]) & (y == 0)).sum())   # extra false alarms
    total_pos   = int((y == 1).sum())

    result = {
        "label": label, "test_year": train_max_year + 1,
        "test_n": int(len(test_df)), "test_positives": total_pos,
        "thresholds": meta,
        "xgboost_alone": xgb_m,
        "expert_system": expert_m,
        "recovered_outbreaks": rescued_pos,
        "added_false_alarms": rescued_neg,
        "sensitivity_gain": round(expert_m["sensitivity"] - xgb_m["sensitivity"], 4),
        "ppv_change":       round(expert_m["ppv"] - xgb_m["ppv"], 4),
    }
    print(f"\n  [{label}]  test={label.split('=')[-1]}  pos={total_pos}/{len(test_df)}")
    print(f"    XGBoost alone : Sens={xgb_m['sensitivity']:.3f}  FAR={xgb_m['false_alarm_rate']:.3f}  PPV={xgb_m['ppv']:.3f}")
    print(f"    + Expert layer: Sens={expert_m['sensitivity']:.3f}  FAR={expert_m['false_alarm_rate']:.3f}  PPV={expert_m['ppv']:.3f}")
    print(f"    Recovered outbreaks: {rescued_pos} (+{result['sensitivity_gain']:.3f} sensitivity)"
          f" at cost of {rescued_neg} added false alarms")
    return result


def main():
    print("=== Knowledge-Based Expert Inference Layer ===")
    df = load_features()
    print(f"Loaded {len(df)} state-weeks ({df['epi_year'].min()}–{df['epi_year'].max()})")

    # ── Headline: cross-clade recovery (train ≤2023, test 2024 Clade I) ──────────
    print("\n── Clade-shift stress test (the headline result) ──")
    cross = evaluate_split(df[df["epi_year"] <= 2023], df[df["epi_year"] == 2024],
                           2023, "Cross-clade (train<=2023, test=2024 Clade I)")

    # ── Walk-forward folds (matches ablation.py) ────────────────────────────────
    print("\n── Walk-forward folds ──")
    folds = [(2019, 2020), (2020, 2021), (2021, 2022), (2022, 2023)]
    fold_results = []
    for train_max, test_year in folds:
        tr = df[df["epi_year"] <= train_max]
        te = df[df["epi_year"] == test_year]
        if len(te) == 0 or te[TARGET].sum() == 0:
            continue
        fold_results.append(
            evaluate_split(tr, te, train_max, f"WF train<={train_max}, test={test_year}")
        )

    def _mean(key, sub):
        vals = [fr[sub][key] for fr in fold_results]
        return round(float(np.mean(vals)), 4) if vals else None

    wf_summary = {
        "xgboost_alone": {k: _mean(k, "xgboost_alone")
                          for k in ["sensitivity", "false_alarm_rate", "ppv"]},
        "expert_system": {k: _mean(k, "expert_system")
                          for k in ["sensitivity", "false_alarm_rate", "ppv"]},
        "mean_recovered_outbreaks": round(float(np.mean([fr["recovered_outbreaks"] for fr in fold_results])), 2) if fold_results else None,
        "mean_sensitivity_gain": round(float(np.mean([fr["sensitivity_gain"] for fr in fold_results])), 4) if fold_results else None,
    }

    print("\n=== Walk-forward mean ===")
    print(f"  XGBoost alone : Sens={wf_summary['xgboost_alone']['sensitivity']}  "
          f"FAR={wf_summary['xgboost_alone']['false_alarm_rate']}  PPV={wf_summary['xgboost_alone']['ppv']}")
    print(f"  + Expert layer: Sens={wf_summary['expert_system']['sensitivity']}  "
          f"FAR={wf_summary['expert_system']['false_alarm_rate']}  PPV={wf_summary['expert_system']['ppv']}")

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "description": (
            "Knowledge-based expert inference layer fusing XGBoost evidence with "
            "codified epidemiological rules. Escalation-only (fail-safe); thresholds "
            "fitted on training folds (no leakage)."
        ),
        "knowledge_base": ExpertInferenceEngine.KNOWLEDGE_BASE,
        "engine_config": {"floor_frac": 0.40, "votes_required": 2,
                          "alert_rule": "prob>=youden OR (prob>=0.4*youden AND corroborating_votes>=2)"},
        "clade_shift_headline": cross,
        "walk_forward_folds": fold_results,
        "walk_forward_summary": wf_summary,
    }
    os.makedirs("p4_early_warning/models", exist_ok=True)
    path = "p4_early_warning/models/expert_system_results.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved → {path}")


if __name__ == "__main__":
    main()
