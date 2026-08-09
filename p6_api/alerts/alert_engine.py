"""
P6 Cross-Border Alert Engine.

Two alert tiers:
  1. Border-state alerts: border state at critical/red/amber → notify neighboring countries
  2. National advisory:   ≥25% of Nigerian states at critical/red → notify ALL neighbors

Alerts are inserted into cross_border_alerts with delivery_status='delivered' (simulated webhook).
"""

import os
import json
import decimal
import pathlib
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

from border_states import (
    BORDER_COUNTRIES, STATE_TO_COUNTRIES, RECOMMENDED_ACTIONS,
    NATIONAL_ADVISORY_THRESHOLD,
)

load_dotenv(pathlib.Path(__file__).resolve().parent.parent.parent / ".env")

BORDER_ALERT_TIERS    = {"critical", "red", "amber"}
NATIONAL_ALERT_TIERS  = {"critical", "red"}


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        return super().default(o)


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert(cur, rec: dict) -> None:
    cur.execute("""
        INSERT INTO cross_border_alerts (
            alert_week, alert_year, country_code, country_name,
            border_state_id, border_state_name, risk_tier, risk_prob,
            recommended_action, alert_payload, delivered_at, delivery_status
        ) VALUES (
            %(alert_week)s, %(alert_year)s, %(country_code)s, %(country_name)s,
            %(border_state_id)s, %(border_state_name)s, %(risk_tier)s, %(risk_prob)s,
            %(recommended_action)s, %(alert_payload)s, %(delivered_at)s, %(delivery_status)s
        )
        ON CONFLICT (alert_year, alert_week, country_code, border_state_id) DO UPDATE
            SET risk_prob         = EXCLUDED.risk_prob,
                risk_tier         = EXCLUDED.risk_tier,
                alert_payload     = EXCLUDED.alert_payload,
                delivered_at      = EXCLUDED.delivered_at,
                delivery_status   = EXCLUDED.delivery_status
    """, rec)


def _make_payload(
    state_id, state_name, risk_tier, risk_prob, rs,
    country_code, country_info, alert_class: str,
) -> dict:
    return {
        "alert_type":     "cross_border_mpox",
        "alert_class":    alert_class,          # "border_state_alert" | "national_advisory"
        "generated_at":   _now_iso(),
        "source_country": "NGA",
        "source_system":  "SmartMpox-Nigeria",
        "border_state":   {"state_id": state_id, "state_name": state_name},
        "risk_assessment": {
            "epi_year":         rs["epi_year"],
            "epi_week":         rs["epi_week"],
            "week_start_date":  rs["week_start_date"].isoformat() if rs.get("week_start_date") else None,
            "risk_tier":        risk_tier,
            "risk_probability": risk_prob,
            "model_version":    rs.get("model_version"),
            "top_features": [
                {"feature": rs.get("top_feature_1"), "shap": float(rs["top_feature_1_shap"] or 0)},
                {"feature": rs.get("top_feature_2"), "shap": float(rs["top_feature_2_shap"] or 0)},
                {"feature": rs.get("top_feature_3"), "shap": float(rs["top_feature_3_shap"] or 0)},
            ],
            "cusum_signal": rs.get("cusum_signal"),
            "ears_signal":  rs.get("ears_signal"),
        },
        "recipient_country": {
            "code":           country_code,
            "name":           country_info["name"],
            "capital":        country_info["capital"],
            "health_contact": country_info["health_contact"],
        },
        "recommended_action": RECOMMENDED_ACTIONS.get(risk_tier, ""),
        "api_endpoint": "https://tspwkxyiralnukmefagl.supabase.co/rest/v1/api_latest_risk",
        "data_licence":  "CC-BY-4.0",
    }


def run_alert_engine(dry_run: bool = False) -> list[dict]:
    """
    Scan latest risk scores, generate cross-border alerts, insert into DB.
    Returns list of alert record dicts generated this run.
    """
    conn = get_conn()
    cur  = conn.cursor()

    # ── fetch all latest risk scores ──────────────────────────────────────────
    cur.execute("""
        SELECT r.state_id, s.state_name, r.epi_year, r.epi_week,
               r.week_start_date, r.risk_prob, r.risk_tier,
               r.model_version, r.top_feature_1, r.top_feature_1_shap,
               r.top_feature_2, r.top_feature_2_shap,
               r.top_feature_3, r.top_feature_3_shap,
               r.cusum_signal, r.ears_signal
        FROM latest_risk_scores r
        JOIN ref_states s USING (state_id)
        ORDER BY r.risk_prob DESC
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    all_scores = [dict(zip(cols, r)) for r in rows]

    epi_year = all_scores[0]["epi_year"] if all_scores else 2024
    epi_week = all_scores[0]["epi_week"] if all_scores else 52

    alerts_generated = []
    border_state_ids = list(STATE_TO_COUNTRIES.keys())

    # ── 1. Border-state alerts (critical / red / amber) ───────────────────────
    border_scores = [
        rs for rs in all_scores
        if rs["state_id"] in border_state_ids
        and rs["risk_tier"] in BORDER_ALERT_TIERS
    ]

    alerted_countries = set()
    for rs in border_scores:
        state_id   = rs["state_id"]
        state_name = rs["state_name"]
        risk_tier  = rs["risk_tier"]
        risk_prob  = float(rs["risk_prob"])

        for country_code in STATE_TO_COUNTRIES.get(state_id, []):
            country_info = BORDER_COUNTRIES[country_code]
            alerted_countries.add(country_code)

            payload = _make_payload(
                state_id, state_name, risk_tier, risk_prob, rs,
                country_code, country_info, "border_state_alert",
            )
            rec = {
                "alert_week":         epi_week,
                "alert_year":         epi_year,
                "country_code":       country_code,
                "country_name":       country_info["name"],
                "border_state_id":    state_id,
                "border_state_name":  state_name,
                "risk_tier":          risk_tier,
                "risk_prob":          risk_prob,
                "recommended_action": payload["recommended_action"],
                "alert_payload":      json.dumps(payload, cls=_DecimalEncoder),
                "delivered_at":       _now_iso(),
                "delivery_status":    "delivered",
            }
            alerts_generated.append(rec)
            if not dry_run:
                _upsert(cur, rec)

    # ── 2. National advisory (when ≥25% of states are critical/red) ──────────
    n_total      = len(all_scores)
    n_high_risk  = sum(1 for rs in all_scores if rs["risk_tier"] in NATIONAL_ALERT_TIERS)
    high_fraction = n_high_risk / n_total if n_total else 0

    if high_fraction >= NATIONAL_ADVISORY_THRESHOLD:
        # Use the highest-risk border state as the representative for unalerted countries
        rep_rs = all_scores[0]   # already sorted by risk_prob DESC
        action = (
            f"National situational awareness: {n_high_risk}/{n_total} Nigerian states "
            f"({100*high_fraction:.0f}%) at critical/red risk tier. "
            "Review border health posts and regional IHR notification obligations."
        )
        for country_code, country_info in BORDER_COUNTRIES.items():
            if country_code in alerted_countries:
                continue   # already has a border-state alert; don't double-count
            # Use border_state_id=0 as sentinel for national-level alert
            payload = {
                "alert_type":      "cross_border_mpox",
                "alert_class":     "national_advisory",
                "generated_at":    _now_iso(),
                "source_country":  "NGA",
                "source_system":   "SmartMpox-Nigeria",
                "national_stats": {
                    "total_states":        n_total,
                    "high_risk_states":    n_high_risk,
                    "high_risk_fraction":  round(high_fraction, 4),
                    "epi_year":            epi_year,
                    "epi_week":            epi_week,
                },
                "recipient_country": {
                    "code":           country_code,
                    "name":           country_info["name"],
                    "capital":        country_info["capital"],
                    "health_contact": country_info["health_contact"],
                },
                "recommended_action": action,
                "api_endpoint": "https://tspwkxyiralnukmefagl.supabase.co/rest/v1/api_latest_risk",
                "data_licence":  "CC-BY-4.0",
            }
            rec = {
                "alert_week":         epi_week,
                "alert_year":         epi_year,
                "country_code":       country_code,
                "country_name":       country_info["name"],
                "border_state_id":    None,    # national-level, no specific border state
                "border_state_name":  "National (all states)",
                "risk_tier":          "red",   # national burden is red-level
                "risk_prob":          round(high_fraction, 4),
                "recommended_action": action,
                "alert_payload":      json.dumps(payload),
                "delivered_at":       _now_iso(),
                "delivery_status":    "delivered",
            }
            alerts_generated.append(rec)
            if not dry_run:
                cur.execute("""
                    INSERT INTO cross_border_alerts (
                        alert_week, alert_year, country_code, country_name,
                        border_state_id, border_state_name, risk_tier, risk_prob,
                        recommended_action, alert_payload, delivered_at, delivery_status
                    ) VALUES (
                        %(alert_week)s, %(alert_year)s, %(country_code)s, %(country_name)s,
                        %(border_state_id)s, %(border_state_name)s, %(risk_tier)s, %(risk_prob)s,
                        %(recommended_action)s, %(alert_payload)s, %(delivered_at)s, %(delivery_status)s
                    )
                    ON CONFLICT (alert_year, alert_week, country_code, border_state_id) DO NOTHING
                """, rec)

    if not dry_run:
        conn.commit()
    cur.close()
    conn.close()
    return alerts_generated


def print_summary(alerts: list[dict]) -> None:
    by_country: dict[str, list] = {}
    for a in alerts:
        by_country.setdefault(a["country_code"], []).append(a)

    ts = _now_iso()[:10]
    print(f"\n{'='*64}")
    print(f"  Cross-Border Alert Engine — {ts}")
    print(f"{'='*64}")
    print(f"  Alerts generated : {len(alerts)}")
    print(f"  Countries alerted: {len(by_country)}  ({', '.join(sorted(by_country))})\n")

    for code in sorted(by_country):
        recs = by_country[code]
        print(f"  [{code}] {recs[0]['country_name']}")
        for r in recs:
            print(f"    {r['border_state_name']:<24}  tier={r['risk_tier']:<10}  "
                  f"P={r['risk_prob']:.3f}  status={r['delivery_status']}")

    print(f"\n  Delivery status: all delivered (simulated webhook)")
    print(f"{'='*64}\n")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    if dry:
        print("[DRY RUN] No DB writes.")
    alerts = run_alert_engine(dry_run=dry)
    print_summary(alerts)
    if not dry and alerts:
        print(f"Inserted/updated {len(alerts)} alert records in cross_border_alerts.")
