"""
Simulated Niger Republic health authority API consumer.
Queries the SmartMpox Nigeria public API for risk data and cross-border alerts.
"""

import requests

SUPABASE_URL  = "https://tspwkxyiralnukmefagl.supabase.co"
SUPABASE_ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRzcHdreHlpcmFsbnVrbWVmYWdsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODYyMDMwNTYsImV4cCI6MjEwMTc3OTA1Nn0"
    ".Jcmy5vwAjybjv5rkWX6r5LHC0-9ulQtVuUX1bWiH70s"
)

HEADERS = {
    "apikey":        SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
}

NIGER_CODE    = "NER"
BORDER_STATES = [8, 17, 19, 20, 21, 33, 35, 36]  # Borno, Jigawa, Kano, Katsina, Kebbi, Sokoto, Yobe, Zamfara

print("=" * 60)
print("  SmartMpox Consumer: NIGER REPUBLIC (NER)")
print("  Direction Générale de la Santé Publique")
print("=" * 60)

# ── 1. Risk scores for Niger border states ─────────────────────────────────
print("\n[1] Fetching risk scores for Niger border states...")
params = {
    "state_id": f"in.({','.join(str(s) for s in BORDER_STATES)})",
    "order":    "risk_prob.desc",
    "select":   "state_name,epi_year,epi_week,risk_tier,risk_prob,top_feature_1",
}
r = requests.get(f"{SUPABASE_URL}/rest/v1/api_latest_risk", headers=HEADERS, params=params)
r.raise_for_status()
states = r.json()

print(f"  Retrieved {len(states)} border-state risk records")
print(f"  {'State':<20} {'Tier':<10} {'P(outbreak)':>12}")
print(f"  {'-'*44}")
for s in states:
    print(f"  {s['state_name']:<20} {s['risk_tier']:<10} {s['risk_prob']:>12.4f}")

# ── 2. Cross-border alerts for Niger ──────────────────────────────────────
print("\n[2] Fetching cross-border alerts for Niger Republic...")
params2 = {
    "country_code": f"eq.{NIGER_CODE}",
    "order":        "risk_prob.desc",
    "select":       "border_state_name,risk_tier,risk_prob,recommended_action,generated_at,delivery_status",
}
r2 = requests.get(f"{SUPABASE_URL}/rest/v1/cross_border_alerts", headers=HEADERS, params=params2)
r2.raise_for_status()
alerts = r2.json()

print(f"  Active alerts: {len(alerts)}")
for a in alerts:
    print(f"    [{a['risk_tier'].upper()}] {a['border_state_name']}: "
          f"P={a['risk_prob']:.3f}  status={a['delivery_status']}")
    print(f"      Action: {a['recommended_action'][:90]}")

# ── 3. High-risk state drill-down (risk_tier=critical) ───────────────────
print("\n[3] Pulling all Nigerian states at critical tier...")
params3 = {
    "risk_tier": "eq.critical",
    "order":     "risk_prob.desc",
    "select":    "state_name,risk_tier,risk_prob,top_feature_1,top_feature_1_shap",
}
r3 = requests.get(f"{SUPABASE_URL}/rest/v1/api_latest_risk", headers=HEADERS, params=params3)
r3.raise_for_status()
crit = r3.json()
print(f"  Critical-tier states: {len(crit)}")
for c in crit[:5]:
    print(f"    {c['state_name']:<22} P={c['risk_prob']:.4f}  "
          f"top_feature={c['top_feature_1']}")

print("\n[OK] Niger consumer completed successfully.\n")
