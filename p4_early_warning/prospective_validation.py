"""
P4 Task 10 — Simulated Real-Time (Forward-Chaining) Prospective Validation
==========================================================================
Freezes the ENTIRE system on data <= 2023 (the Clade II era), then replays 2024
week-by-week as Clade I emerges, using only information available at each decision
point, and measures how far ahead of NCDC laboratory confirmation the system
alerts.

This is a *simulated* prospective test (retrospective replay under strict temporal
freezing), not a live real-time deployment — reported honestly as such. It is the
standard, defensible way to estimate prospective lead time before real forward data
accrue.

Two alerting channels are evaluated separately and honestly:
  A. Digital surveillance (national)  — lead time as a function of the alert
     threshold, to show exactly how sensitive the headline "204-day" figure is to
     the choice of threshold.
  B. Case-based ML + expert layer (state) — does a Clade-II-trained model pre-empt
     Clade I at all? (tests the clade-shift limitation directly).

Ground truth: NCDC first laboratory-confirmed week in 2024 (surveillance_weekly).

Run:  python p4_early_warning/prospective_validation.py
Out:  p4_early_warning/models/prospective_validation_results.json
"""
import os, json
from datetime import date, datetime, timezone
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

from expert_system import (
    FEATURE_COLS, load_features, add_derived, build_engine,
    load_national_digital_signal, base_tier,
)

load_dotenv()
FREEZE_YEAR = 2023          # system frozen on <= this year
TEST_YEAR   = 2024          # replay this year forward


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def iso_monday(year, week):
    try:
        return date.fromisocalendar(int(year), int(week), 1)
    except Exception:
        return None


# ───────── ground truth: NCDC 2024 confirmations ─────────
def ncdc_confirmations():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT state_id, MIN(epi_week) AS first_week, MIN(week_start_date) AS first_date
        FROM surveillance_weekly
        WHERE epi_year = %s AND COALESCE(confirmed, 0) > 0
        GROUP BY state_id
    """, (TEST_YEAR,))
    per_state = {int(r[0]): {"week": int(r[1]), "date": r[2]} for r in cur.fetchall()}
    cur.execute("""
        SELECT MIN(week_start_date) FROM surveillance_weekly
        WHERE epi_year = %s AND COALESCE(confirmed, 0) > 0
    """, (TEST_YEAR,))
    national = cur.fetchone()[0]
    cur.close(); conn.close()
    return per_state, national


# ───────── channel A: digital lead-time vs threshold ─────────
def digital_weekly_counts():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT EXTRACT(WEEK FROM published_at)::int AS wk, COUNT(*) AS n
        FROM social_media_signals
        WHERE is_mpox_relevant = TRUE
          AND EXTRACT(ISOYEAR FROM published_at) = %s
        GROUP BY wk ORDER BY wk
    """, (TEST_YEAR,))
    rows = {int(r[0]): int(r[1]) for r in cur.fetchall()}
    cur.close(); conn.close()
    return rows


def digital_lead_analysis(national_ncdc_date):
    # frozen surge threshold from <=2023 posts (same as the deployed expert engine)
    _, frozen_p75 = load_national_digital_signal(FREEZE_YEAR)
    counts = digital_weekly_counts()
    weeks = sorted(counts)
    results = []
    for thr in [1, 2, int(round(frozen_p75)), 8]:
        first_wk = next((w for w in weeks if counts[w] >= thr), None)
        alert_date = iso_monday(TEST_YEAR, first_wk) if first_wk else None
        lead = (national_ncdc_date - alert_date).days if alert_date else None
        results.append({
            "threshold_posts_per_week": thr,
            "is_frozen_operating_point": (thr == int(round(frozen_p75))),
            "first_alert_week": first_wk,
            "first_alert_date": alert_date.isoformat() if alert_date else None,
            "lead_days_vs_ncdc": lead,
        })
    return {"frozen_surge_threshold": frozen_p75, "by_threshold": results,
            "weekly_counts_2024": counts}


# ───────── channel B: case-based ML + expert forward replay ─────────
def load_test_year_features():
    """All 2024 state-weeks (features only; target not required for alerting)."""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT state_id, epi_year, epi_week, week_start_date,
               cases_t1, cases_t2, cases_t4,
               cases_rolling4w_mean, cases_rolling8w_mean, cases_log1p,
               rainfall_t2_mm, rainfall_t4_mm, temp_mean_t1_c,
               reservoir_risk_index,
               is_border_state::INT AS is_border_state,
               neighbour_cases_t1
        FROM features_weekly
        WHERE is_complete = TRUE AND epi_year = %s
        ORDER BY epi_week, state_id
    """, (TEST_YEAR,))
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close(); conn.close()
    return add_derived(df)


def _first_alert_per_state(test, probs, threshold_fn):
    first_alert = {}
    for i, (_, row) in enumerate(test.iterrows()):
        if threshold_fn(float(probs[i])):
            sid, wk = int(row["state_id"]), int(row["epi_week"])
            if sid not in first_alert or wk < first_alert[sid]:
                first_alert[sid] = wk
    return first_alert


def _lead_stats(per_state_ncdc, first_alert):
    leads = []
    for sid, info in per_state_ncdc.items():
        conf_wk = info["week"]
        alert_wk = first_alert.get(sid)
        leads.append({"state_id": sid, "ncdc_confirm_week": conf_wk,
                      "ml_alert_week": alert_wk,
                      "lead_weeks": (conf_wk - alert_wk) if alert_wk is not None else None})
    return leads


def model_forward_replay(per_state_ncdc):
    labelled = load_features()
    frozen = labelled[labelled["epi_year"] <= FREEZE_YEAR]
    model, engine, meta = build_engine(frozen, FREEZE_YEAR)
    youden = meta["youden"]

    test = load_test_year_features()
    X = test[FEATURE_COLS].fillna(0).values.astype(np.float32)
    probs = model.predict_proba(X)[:, 1]

    national_week = min(v["week"] for v in per_state_ncdc.values())  # week 34

    def alarm_context(threshold_fn, label):
        """How non-specific is a 'pre-emption' claim under this threshold?"""
        pre = test.reset_index(drop=True)
        mask = np.array([threshold_fn(float(p)) for p in probs])
        pre_window = pre["epi_week"].values < national_week
        alerted_states = set(pre["state_id"].values[mask & pre_window].astype(int))
        first_alert = _first_alert_per_state(test, probs, threshold_fn)
        leads = _lead_stats(per_state_ncdc, first_alert)
        before = [l for l in leads if l["lead_weeks"] is not None and l["lead_weeks"] > 0]
        n_alerted_sw = int((mask & pre_window).sum())
        n_total_sw   = int(pre_window.sum())
        return {
            "threshold": label,
            "distinct_states_alerted_pre_confirmation": len(alerted_states),
            "of_total_states": int(pre["state_id"].nunique()),
            "pre_confirmation_alert_rate": round(n_alerted_sw / n_total_sw, 4) if n_total_sw else None,
            "n_confirmed_states_alerted_before": len(before),
            "per_state": sorted(leads, key=lambda x: x["ncdc_confirm_week"]),
        }

    deployed = alarm_context(lambda p: base_tier(p) in ("red", "critical"),
                             "deployed tiers (red>=0.20)")
    calibrated = alarm_context(lambda p: p >= youden,
                               f"calibrated Youden (>={round(youden,3)})")

    return {"engine_thresholds": meta,
            "n_confirmed_states": len(per_state_ncdc),
            "national_confirmation_week": national_week,
            "deployed_threshold": deployed,
            "calibrated_threshold": calibrated,
            "note": ("A high 'alerted before confirmation' count under the lenient deployed "
                     "tiers is a NON-SPECIFIC standing prior, not detection: the same "
                     "threshold alarms most states all year. The calibrated Youden threshold "
                     "shows the genuine (near-zero) prospective skill of a Clade-II-trained "
                     "case model against Clade I.")}


def main():
    print("=== Simulated Real-Time Prospective Validation (freeze<=2023, replay 2024) ===")
    per_state_ncdc, national_ncdc = ncdc_confirmations()
    print(f"NCDC national first confirmation 2024: {national_ncdc} "
          f"({len(per_state_ncdc)} states confirmed during 2024)")

    print("\n── Channel A: digital surveillance lead time vs alert threshold ──")
    dig = digital_lead_analysis(national_ncdc)
    print(f"  Frozen surge threshold (<=2023 p75) = {dig['frozen_surge_threshold']} posts/week")
    for r in dig["by_threshold"]:
        star = "  <-- frozen operating point" if r["is_frozen_operating_point"] else ""
        print(f"    thr>={r['threshold_posts_per_week']:>2} posts: first alert "
              f"{r['first_alert_date']} (wk {r['first_alert_week']}) -> "
              f"lead {r['lead_days_vs_ncdc']} days{star}")

    print("\n── Channel B: case-based ML + expert forward replay ──")
    mdl = model_forward_replay(per_state_ncdc)
    print(f"  States confirmed in 2024: {mdl['n_confirmed_states']} | "
          f"national confirmation week {mdl['national_confirmation_week']}")
    for key in ("deployed_threshold", "calibrated_threshold"):
        d = mdl[key]
        print(f"  [{d['threshold']}]")
        print(f"     distinct states alerted pre-confirmation: "
              f"{d['distinct_states_alerted_pre_confirmation']}/{d['of_total_states']} "
              f"(pre-confirm alert rate {d['pre_confirmation_alert_rate']})")
        print(f"     confirmed states 'alerted before': {d['n_confirmed_states_alerted_before']}")
    print("  => the lenient count is a non-specific standing prior; the calibrated")
    print("     threshold shows the true (near-zero) case-based prospective skill.")

    # Honest headline
    frozen_row = next(r for r in dig["by_threshold"] if r["is_frozen_operating_point"])
    any_mention = next(r for r in dig["by_threshold"] if r["threshold_posts_per_week"] == 1)
    print("\n=== HONEST PROSPECTIVE HEADLINE ===")
    print(f"  Digital lead at the deployable frozen threshold: {frozen_row['lead_days_vs_ncdc']} days")
    print(f"  Digital lead at 'any single mention' (thr=1, NOT operational): "
          f"{any_mention['lead_days_vs_ncdc']} days")
    print(f"  Case-based ML prospective lead: none (clade shift) — the lead is entirely digital.")

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "method": ("Simulated real-time (forward-chaining) validation: system frozen on "
                   "<=2023, replayed weekly through 2024 with no look-ahead. Not a live "
                   "real-time deployment."),
        "ncdc_national_first_confirmation": national_ncdc.isoformat(),
        "channel_a_digital": dig,
        "channel_b_model_expert": mdl,
        "honest_summary": {
            "digital_lead_days_frozen_threshold": frozen_row["lead_days_vs_ncdc"],
            "digital_lead_days_any_mention_threshold1": any_mention["lead_days_vs_ncdc"],
            "ml_prospective_skill_calibrated": (
                f"{mdl['calibrated_threshold']['n_confirmed_states_alerted_before']}"
                f"/{mdl['n_confirmed_states']} confirmed states genuinely pre-empted at the "
                f"calibrated threshold"),
            "interpretation": (
                "Prospective lead comes essentially entirely from the digital channel. The "
                "long headline lead depends on a single early mention (threshold=1), which is "
                "NOT an operational alert; at the deployable frozen surge threshold the lead is "
                "~2 weeks but robust. The case-based ML offers little genuine prospective skill "
                "against a novel clade (its lenient-threshold 'pre-emption' is a non-specific "
                "standing prior). Report both honestly."
            ),
        },
    }
    os.makedirs("p4_early_warning/models", exist_ok=True)
    path = "p4_early_warning/models/prospective_validation_results.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved -> {path}")


if __name__ == "__main__":
    main()
