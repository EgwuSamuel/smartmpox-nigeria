"""
Border state → neighboring country mapping for Nigeria.
Used by the alert engine to determine which countries to notify.
"""

# ISO 3166-1 alpha-3 country codes for Nigeria's land-border neighbors
BORDER_COUNTRIES = {
    "CMR": {
        "name": "Cameroon",
        "capital": "Yaoundé",
        "health_contact": "Direction de la Lutte contre la Maladie, Ministère de la Santé Publique",
        "border_states": [2, 8, 9, 34],   # Adamawa, Borno, Cross River, Taraba
    },
    "BEN": {
        "name": "Benin Republic",
        "capital": "Cotonou",
        "health_contact": "Direction Nationale de la Santé Publique",
        "border_states": [22, 23, 24, 27, 30],  # Kogi, Kwara, Lagos, Ogun, Oyo
    },
    "NER": {
        "name": "Niger Republic",
        "capital": "Niamey",
        "health_contact": "Direction Générale de la Santé Publique",
        "border_states": [8, 17, 19, 20, 21, 33, 35, 36],  # Borno, Jigawa, Kano, Katsina, Kebbi, Sokoto, Yobe, Zamfara
    },
    "TCD": {
        "name": "Chad",
        "capital": "N'Djamena",
        "health_contact": "Direction de la Lutte contre les Maladies",
        "border_states": [8, 35],   # Borno, Yobe
    },
}

# Reverse map: state_id → list of neighboring countries
STATE_TO_COUNTRIES: dict[int, list[str]] = {}
for _code, _info in BORDER_COUNTRIES.items():
    for _sid in _info["border_states"]:
        STATE_TO_COUNTRIES.setdefault(_sid, []).append(_code)

# Alert tiers that trigger cross-border notification
ALERT_TIERS = {"critical", "red"}

RECOMMENDED_ACTIONS = {
    "critical": "Activate cross-border emergency alert: heighten surveillance, review case definitions, prepare response teams.",
    "red":      "Heighten surveillance in border districts and review entry screening protocols.",
    "amber":    "Monitor border health posts and review case detection capacity.",
}

# When this fraction of all Nigerian states are critical/red, issue a
# national-level situational awareness notice to ALL neighboring countries.
NATIONAL_ADVISORY_THRESHOLD = 0.25   # 25% of 37 states = ~9 states
