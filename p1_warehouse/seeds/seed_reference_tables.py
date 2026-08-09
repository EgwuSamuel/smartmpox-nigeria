"""
Seed ref_data_sources and ref_states with baseline reference data.
Run once: python p1_warehouse/seeds/seed_reference_tables.py
"""
import os, yaml, psycopg2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
conn.autocommit = True
cur = conn.cursor()

# ── 1. ref_data_sources ────────────────────────────────────────────────────
print("Seeding ref_data_sources ...")
sources_path = Path(__file__).parent.parent / "config" / "sources.yaml"
with open(sources_path, encoding="utf-8") as f:
    sources = yaml.safe_load(f)["sources"]

for s in sources:
    cur.execute("""
        INSERT INTO ref_data_sources
            (source_code, source_name, source_url, licence, update_frequency,
             requires_auth, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_code) DO UPDATE SET
            source_name       = EXCLUDED.source_name,
            source_url        = EXCLUDED.source_url,
            licence           = EXCLUDED.licence,
            update_frequency  = EXCLUDED.update_frequency,
            requires_auth     = EXCLUDED.requires_auth,
            notes             = EXCLUDED.notes;
    """, (
        s["code"], s["name"], s["url"], s["licence"],
        s["update_frequency"], s["requires_auth"], s.get("notes", "")
    ))

cur.execute("SELECT count(*) FROM ref_data_sources;")
print(f"  ref_data_sources: {cur.fetchone()[0]} rows")

# ── 2. ref_states ──────────────────────────────────────────────────────────
print("Seeding ref_states ...")

STATES = [
    # (state_code, state_name, geopolitical_zone, is_border_state)
    ("AB", "Abia",            "South East",  False),
    ("AD", "Adamawa",         "North East",  True),
    ("AK", "Akwa Ibom",       "South South", False),
    ("AN", "Anambra",         "South East",  False),
    ("BA", "Bauchi",          "North East",  False),
    ("BY", "Bayelsa",         "South South", False),
    ("BE", "Benue",           "North Central", False),
    ("BO", "Borno",           "North East",  True),
    ("CR", "Cross River",     "South South", True),
    ("DE", "Delta",           "South South", False),
    ("EB", "Ebonyi",          "South East",  False),
    ("ED", "Edo",             "South South", False),
    ("EK", "Ekiti",           "South West",  False),
    ("EN", "Enugu",           "South East",  False),
    ("GO", "Gombe",           "North East",  False),
    ("IM", "Imo",             "South East",  False),
    ("JI", "Jigawa",          "North West",  True),
    ("KD", "Kaduna",          "North West",  False),
    ("KN", "Kano",            "North West",  False),
    ("KT", "Katsina",         "North West",  True),
    ("KE", "Kebbi",           "North West",  True),
    ("KO", "Kogi",            "North Central", False),
    ("KW", "Kwara",           "North Central", False),
    ("LA", "Lagos",           "South West",  False),
    ("NA", "Nasarawa",        "North Central", False),
    ("NI", "Niger",           "North Central", False),
    ("OG", "Ogun",            "South West",  False),
    ("ON", "Ondo",            "South West",  False),
    ("OS", "Osun",            "South West",  False),
    ("OY", "Oyo",             "South West",  False),
    ("PL", "Plateau",         "North Central", False),
    ("RI", "Rivers",          "South South", False),
    ("SO", "Sokoto",          "North West",  True),
    ("TA", "Taraba",          "North East",  True),
    ("YO", "Yobe",            "North East",  True),
    ("ZA", "Zamfara",         "North West",  False),
    ("FC", "FCT Abuja",       "North Central", False),
]

for code, name, zone, is_border in STATES:
    cur.execute("""
        INSERT INTO ref_states (state_code, state_name, geopolitical_zone, is_border_state)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (state_code) DO UPDATE SET
            state_name         = EXCLUDED.state_name,
            geopolitical_zone  = EXCLUDED.geopolitical_zone,
            is_border_state    = EXCLUDED.is_border_state;
    """, (code, name, zone, is_border))

cur.execute("SELECT count(*) FROM ref_states;")
print(f"  ref_states: {cur.fetchone()[0]} rows")

cur.close()
conn.close()
print("\nDone. Reference tables seeded.")
