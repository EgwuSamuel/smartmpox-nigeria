"""
Simulated Benin Republic health authority API consumer.
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

BENIN_CODE    = "BEN"
BORDER_STATES = [22, 23, 24, 27, 30]  # Kogi, Kwara, Lagos, Ogun, Oyo

print("=" * 60)
print("  SmartMpox Consumer: BENIN REPUBLIC (BEN)")
print("  Direction Nationale de la Santé Publique")
print("=" * 60)

# ── 1. Risk scores for Benin border states ─────────────────────────────────
print("\n[1] Fetching risk scores for Benin border states...")
params = {
    "state_id": f"in.({','.join(str(s) for s in BORDER_STATES)})",
    "order":    "risk_prob.desc",
    "select":   "state_name,epi_year,epi_week,risk_tier,risk_prob,top_feature_1,cusum_signal",
}
r = requests.get(f"{SUPABASE_URL}/rest/v1/api_latest_risk", headers=HEADERS, params=params)
r.raise_for_status()
states = r.json()

print(f"  Retrieved {len(states)} border-state risk records")
print(f"  {'State':<20} {'Tier':<10} {'P(outbreak)':>12}")
print(f"  {'-'*44}")
for s in states:
    print(f"  {s['state_name']:<20} {s['risk_tier']:<10} {s['risk_prob']:>12.4f}")

# ── 2. Cross-border alerts for Benin ──────────────────────────────────────
print("\n[2] Fetching cross-border alerts for Benin Republic...")
params2 = {
    "country_code": f"eq.{BENIN_CODE}",
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

# ── 3. National risk overview (all 37 states) ────────────────────────────
print("\n[3] Fetching national risk tier summary...")
params3 = {
    "order":  "risk_prob.desc",
    "select": "state_name,risk_tier,risk_prob",
}
r3 = requests.get(f"{SUPABASE_URL}/rest/v1/api_latest_risk", headers=HEADERS, params=params3)
r3.raise_for_status()
all_states = r3.json()
tier_counts = {}
for s in all_states:
    tier_counts[s["risk_tier"]] = tier_counts.get(s["risk_tier"], 0) + 1

print(f"  National tier distribution (all 37 states):")
for tier in ["critical", "red", "amber", "green"]:
    print(f"    {tier:<10}: {tier_counts.get(tier, 0)} states")

print("\n[OK] Benin consumer completed successfully.\n")
