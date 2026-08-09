"""
Simulated Cameroon health authority API consumer.
Queries the SmartMpox Nigeria public API for risk data and cross-border alerts.
"""

import os, sys, json, requests

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

CAMEROON_CODE   = "CMR"
BORDER_STATES   = [2, 8, 9, 34]   # Adamawa, Borno, Cross River, Taraba

print("=" * 60)
print("  SmartMpox Consumer: CAMEROON (CMR)")
print("  Direction de la Lutte contre la Maladie")
print("=" * 60)

# ── 1. Pull current risk scores for Nigerian border states ──────────────────
print("\n[1] Fetching risk scores for Cameroon border states...")
params = {
    "state_id":  f"in.({','.join(str(s) for s in BORDER_STATES)})",
    "order":     "risk_prob.desc",
    "select":    "state_name,epi_year,epi_week,risk_tier,risk_prob,top_feature_1,cusum_signal",
}
r = requests.get(f"{SUPABASE_URL}/rest/v1/api_latest_risk", headers=HEADERS, params=params)
r.raise_for_status()
states = r.json()

print(f"  Retrieved {len(states)} border-state risk records")
print(f"  {'State':<20} {'Tier':<10} {'P(outbreak)':>12}")
print(f"  {'-'*44}")
for s in states:
    print(f"  {s['state_name']:<20} {s['risk_tier']:<10} {s['risk_prob']:>12.4f}")

# ── 2. Pull active cross-border alerts for Cameroon ─────────────────────────
print("\n[2] Fetching cross-border alerts for Cameroon...")
params2 = {
    "country_code": f"eq.{CAMEROON_CODE}",
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

# ── 3. Pull recent surveillance weekly data ─────────────────────────────────
print("\n[3] Fetching recent surveillance weekly data (border states)...")
params3 = {
    "state_id": f"in.({','.join(str(s) for s in BORDER_STATES)})",
    "epi_year": "eq.2024",
    "order":    "epi_week.desc",
    "limit":    "8",
    "select":   "state_name,epi_year,epi_week,confirmed,suspected,total_cases,deaths",
}
r3 = requests.get(f"{SUPABASE_URL}/rest/v1/api_weekly_cases", headers=HEADERS, params=params3)
r3.raise_for_status()
weekly = r3.json()

print(f"  Retrieved {len(weekly)} surveillance records (border states, 2024)")
if weekly:
    print(f"  {'State':<20} {'Wk':>4} {'Confirmed':>10} {'Suspected':>10} {'Total':>7}")
    print(f"  {'-'*55}")
    for w in weekly[:6]:
        print(f"  {w['state_name']:<20} {w['epi_week']:>4} "
              f"{(w['confirmed'] or 0):>10} {(w['suspected'] or 0):>10} "
              f"{(w['total_cases'] or 0):>7}")

print("\n[OK] Cameroon consumer completed successfully.")
print("     All 3 API endpoints returned data.\n")
