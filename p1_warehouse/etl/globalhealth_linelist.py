"""
ETL: Global.health Mpox Linelist → cases_individual
Downloads Nigeria individual case records from the G.h GitHub CSV.
Strategy: delete-and-replace per source (full snapshot, idempotent).
Run: python p1_warehouse/etl/globalhealth_linelist.py [--dry-run] [--limit N]
"""
import os, io, re, json, math, argparse, requests, psycopg2, pandas as pd
from psycopg2.extras import execute_values
from datetime import datetime, timezone
from dotenv import load_dotenv

LINELIST_URL = (
    "https://raw.githubusercontent.com/globaldothealth/monkeypox/"
    "946edb545947af7f5195459ce52bb71d098e240c/latest_deprecated.csv"
)
HEADERS = {"User-Agent": "SmartMpox-Research/1.0 (academic; egwuonucheojosamuel@gmail.com)"}

load_dotenv()

STATE_ALIASES = {
    "Abia": "AB", "Adamawa": "AD", "Akwa Ibom": "AK", "Akwa-Ibom": "AK",
    "Anambra": "AN", "Bauchi": "BA", "Bayelsa": "BY", "Benue": "BE",
    "Borno": "BO", "Cross River": "CR", "Cross-River": "CR", "Delta": "DE",
    "Ebonyi": "EB", "Edo": "ED", "Ekiti": "EK", "Enugu": "EN",
    "Gombe": "GO", "Imo": "IM", "Jigawa": "JI", "Kaduna": "KD",
    "Kano": "KN", "Katsina": "KT", "Kebbi": "KE", "Kogi": "KO",
    "Kwara": "KW", "Lagos": "LA", "Nasarawa": "NA", "Niger": "NI",
    "Ogun": "OG", "Ondo": "ON", "Osun": "OS", "Oyo": "OY",
    "Plateau": "PL", "Rivers": "RI", "Sokoto": "SO", "Taraba": "TA",
    "Yobe": "YO", "Zamfara": "ZA",
    "FCT": "FC", "Abuja": "FC", "Federal Capital Territory": "FC",
}


def parse_age(age_str):
    """Return (age_years_or_None, age_group)."""
    if pd.isna(age_str) or str(age_str).strip().lower() in ("", "unknown", "nan"):
        return None, "unknown"
    s = str(age_str).strip()
    if s.isdigit():
        y = int(s)
        if y < 5:  return y, "<5"
        if y < 15: return y, "5-14"
        if y < 30: return y, "15-29"
        if y < 45: return y, "30-44"
        if y < 60: return y, "45-59"
        return y, "60+"
    if s.endswith("+"):
        lo = int(re.sub(r"[^\d]", "", s))
        if lo >= 60: return None, "60+"
        if lo >= 45: return None, "45-59"
        if lo >= 30: return None, "30-44"
        return None, "unknown"
    m = re.match(r"(\d+)[^\d]+(\d+)", s)
    if m:
        mid = (int(m.group(1)) + int(m.group(2))) // 2
        if mid < 5:  return None, "<5"
        if mid < 15: return None, "5-14"
        if mid < 30: return None, "15-29"
        if mid < 45: return None, "30-44"
        if mid < 60: return None, "45-59"
        return None, "60+"
    return None, "unknown"


def parse_sex(val):
    if pd.isna(val): return "U"
    s = str(val).strip().lower()
    if s in ("m", "male"): return "M"
    if s in ("f", "female"): return "F"
    return "U"


def parse_bool(val):
    if pd.isna(val): return None
    s = str(val).strip().lower()
    if s in ("y", "yes", "true", "1"): return True
    if s in ("n", "no", "false", "0"): return False
    return None


def parse_date(val):
    if pd.isna(val) or str(val).strip().lower() in ("", "na", "nan"): return None
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return None


def parse_symptoms(symptoms_str):
    if pd.isna(symptoms_str) or not str(symptoms_str).strip():
        return None, None, None, None, None, None
    text = str(symptoms_str).lower()
    fever = "fever" in text
    rash = "rash" in text or "skin lesion" in text or "lesion" in text
    lymph = "lymph" in text or "adenopathy" in text
    headache = "headache" in text
    myalgia = any(k in text for k in ("myalgia", "muscle", "arthralgia", "bodyache"))
    known_kw = {"fever", "rash", "skin lesion", "lesion", "lymph", "adenopathy",
                "headache", "myalgia", "muscle", "arthralgia", "bodyache"}
    extras = [t.strip() for t in text.split(",")
              if t.strip() and not any(k in t for k in known_kw)]
    other = ", ".join(extras) or None
    return fever, rash, lymph, headache, myalgia, other


def parse_classification(status_str):
    if pd.isna(status_str): return None
    s = str(status_str).strip().lower()
    if s == "confirmed":  return "confirmed"
    if s == "suspected":  return "suspected"
    if s == "discarded":  return "discarded"
    if s == "probable":   return "probable"
    # omit_error → skip row
    return None


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def get_source_id(cur, code="GLOBAL_HEALTH"):
    cur.execute("SELECT source_id FROM ref_data_sources WHERE source_code = %s", (code,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Source '{code}' not in ref_data_sources. Run seed first.")
    return row[0]


def build_state_lookup(cur):
    cur.execute("SELECT state_id, state_code FROM ref_states;")
    return {row[1]: row[0] for row in cur.fetchall()}


def log_run_start(cur, source_id):
    cur.execute("""
        INSERT INTO etl_run_log (source_id, run_started_at, status)
        VALUES (%s, %s, 'running') RETURNING run_id
    """, (source_id, datetime.now(timezone.utc)))
    return cur.fetchone()[0]


def log_run_finish(cur, run_id, status, rows_fetched=0, rows_inserted=0,
                   rows_rejected=0, error_message=None):
    cur.execute("""
        UPDATE etl_run_log SET
            run_finished_at = %s, status = %s, rows_fetched = %s,
            rows_inserted = %s, rows_rejected = %s, error_message = %s
        WHERE run_id = %s
    """, (datetime.now(timezone.utc), status, rows_fetched,
          rows_inserted, rows_rejected, error_message, run_id))


def fetch_linelist() -> pd.DataFrame:
    print("Downloading Global.health mpox linelist from GitHub ...")
    resp = requests.get(LINELIST_URL, headers=HEADERS, timeout=120)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text), low_memory=False)
    print(f"  Total rows: {len(df):,} | Columns: {list(df.columns)[:8]} ...")

    # Filter to Nigeria rows
    if "Country_ISO3" in df.columns:
        nga = df[df["Country_ISO3"] == "NGA"].copy()
    elif "Country" in df.columns:
        nga = df[df["Country"].str.contains("Nigeria", case=False, na=False)].copy()
    else:
        raise RuntimeError("Cannot find country column. Inspect CSV columns above.")
    print(f"  Nigeria rows: {len(nga):,}")
    return nga


def row_to_record(row, source_id, state_lookup):
    """Map one G.h CSV row → DB record dict. Returns None if unusable."""
    classification = parse_classification(row.get("Status"))
    if classification is None:
        return None

    age_years, age_group = parse_age(row.get("Age"))

    location = str(row.get("Location") or "").strip()
    state_code = STATE_ALIASES.get(location)
    state_id = state_lookup.get(state_code) if state_code else None

    fever, rash, lymph, headache, myalgia, other_sym = parse_symptoms(row.get("Symptoms"))

    conf_method = str(row.get("Confirmation_method") or "").strip()
    lab_tested = bool(conf_method and conf_method.lower() not in ("", "nan", "unknown", "n/a"))
    lab_method_val = conf_method[:30] if lab_tested else None

    outcome_raw = str(row.get("Outcome") or "").strip().lower()
    if outcome_raw in ("death", "died", "dead"):
        outcome_val = "dead"
    elif outcome_raw in ("recovered", "alive", "discharged"):
        outcome_val = "alive"
    else:
        outcome_val = "unknown"

    travel_hist = parse_bool(row.get("Travel_history"))
    travel_cty_raw = str(row.get("Travel_history_country") or "").strip()
    travel_country = travel_cty_raw[:60] if travel_cty_raw.lower() not in ("", "nan") else None

    clade_raw = str(row.get("Genomics_Metadata") or "").strip()
    clade = clade_raw[:10] if clade_raw.lower() not in ("", "nan") else None

    lab_result = "positive" if classification == "confirmed" else None

    return {
        "source_id":               source_id,
        "source_case_id":          str(row.get("ID") or "")[:60] or None,
        "case_classification":     classification,
        "clade":                   clade,
        "date_onset":              parse_date(row.get("Date_onset")),
        "date_reported":           parse_date(row.get("Date_confirmation") or row.get("Date_entry")),
        "date_hospitalised":       parse_date(row.get("Date_hospitalisation")),
        "date_outcome":            parse_date(row.get("Date_death")),
        "country":                 "NGA",
        "state_id":                state_id,
        "location_precision":      "state" if state_id else "country",
        "age_years":               age_years,
        "age_group":               age_group,
        "sex":                     parse_sex(row.get("Gender")),
        "symptom_fever":           fever,
        "symptom_rash":            rash,
        "symptom_lymphadenopathy": lymph,
        "symptom_headache":        headache,
        "symptom_myalgia":         myalgia,
        "symptom_other":           other_sym,
        "travel_history":          travel_hist,
        "travel_country":          travel_country,
        "lab_tested":              lab_tested,
        "lab_method":              lab_method_val,
        "lab_result":              lab_result,
        "outcome":                 outcome_val,
        "vaccinated_smallpox":     None,
        "raw_record":              json.dumps(
            {k: (None if isinstance(v, float) and math.isnan(v) else v)
             for k, v in row.to_dict().items()},
            default=str
        ),
    }


INSERT_SQL = """
    INSERT INTO cases_individual (
        source_id, source_case_id, case_classification, clade,
        date_onset, date_reported, date_hospitalised, date_outcome,
        country, state_id, location_precision,
        age_years, age_group, sex,
        symptom_fever, symptom_rash, symptom_lymphadenopathy,
        symptom_headache, symptom_myalgia, symptom_other,
        travel_history, travel_country,
        lab_tested, lab_method, lab_result,
        outcome, vaccinated_smallpox, raw_record
    ) VALUES (
        %(source_id)s, %(source_case_id)s, %(case_classification)s, %(clade)s,
        %(date_onset)s, %(date_reported)s, %(date_hospitalised)s, %(date_outcome)s,
        %(country)s, %(state_id)s, %(location_precision)s,
        %(age_years)s, %(age_group)s, %(sex)s,
        %(symptom_fever)s, %(symptom_rash)s, %(symptom_lymphadenopathy)s,
        %(symptom_headache)s, %(symptom_myalgia)s, %(symptom_other)s,
        %(travel_history)s, %(travel_country)s,
        %(lab_tested)s, %(lab_method)s, %(lab_result)s,
        %(outcome)s, %(vaccinated_smallpox)s, %(raw_record)s::jsonb
    )
"""


def main():
    parser = argparse.ArgumentParser(description="Load G.h mpox linelist → cases_individual")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--limit",   type=int, default=None, help="Limit to first N Nigeria rows")
    args = parser.parse_args()

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    source_id = get_source_id(cur)
    state_lookup = build_state_lookup(cur)
    run_id = log_run_start(cur, source_id)
    conn.commit()

    try:
        df = fetch_linelist()
        if args.limit:
            df = df.head(args.limit)
        rows_fetched = len(df)

        # Map rows to records; skip country-only rows (no state_id — schema rejects them)
        records, rejected, no_state = [], 0, 0
        for _, row in df.iterrows():
            rec = row_to_record(row, source_id, state_lookup)
            if rec is None:
                rejected += 1
            elif rec["state_id"] is None:
                no_state += 1
            else:
                records.append(rec)
        print(f"  Parsed: {len(records)} valid, {rejected} no-classification, {no_state} country-only (skipped)")

        if args.dry_run:
            print("\n[DRY RUN] Sample records:")
            for r in records[:5]:
                print(f"  {r['source_case_id']} | {r['case_classification']} | "
                      f"{r['date_reported']} | state={r['state_id']} | sex={r['sex']} | "
                      f"age_group={r['age_group']}")
            # Cancel the run_log entry (no write in dry-run)
            conn.rollback()
            return

        # Delete existing G.h rows for clean reload
        cur.execute("DELETE FROM cases_individual WHERE source_id = %s", (source_id,))
        deleted = cur.rowcount

        # Batch insert via execute_values (one round trip for all rows)
        COLS = ("source_id","source_case_id","case_classification","clade",
                "date_onset","date_reported","date_hospitalised","date_outcome",
                "country","state_id","location_precision",
                "age_years","age_group","sex",
                "symptom_fever","symptom_rash","symptom_lymphadenopathy",
                "symptom_headache","symptom_myalgia","symptom_other",
                "travel_history","travel_country",
                "lab_tested","lab_method","lab_result",
                "outcome","vaccinated_smallpox","raw_record")
        template = "(" + ",".join(["%s"] * (len(COLS) - 1)) + ",%s::jsonb)"
        values = [tuple(r[c] for c in COLS) for r in records]

        try:
            execute_values(cur, f"""
                INSERT INTO cases_individual ({",".join(COLS)}) VALUES %s
            """, values, template=template)
            inserted = len(values)
        except Exception as exc:
            conn.rollback()
            print(f"  Batch insert failed: {exc}")
            print("  Falling back to per-row inserts ...")
            cur = conn.cursor()
            cur.execute("DELETE FROM cases_individual WHERE source_id = %s", (source_id,))
            inserted = 0
            for rec in records:
                try:
                    cur.execute("SAVEPOINT sp_row")
                    cur.execute(INSERT_SQL, rec)
                    cur.execute("RELEASE SAVEPOINT sp_row")
                    inserted += 1
                except Exception as row_exc:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_row")
                    rejected += 1
                    print(f"  Skip {rec.get('source_case_id')}: {row_exc}")

        log_run_finish(cur, run_id, "success",
                       rows_fetched=rows_fetched,
                       rows_inserted=inserted,
                       rows_rejected=rejected)
        conn.commit()
        print(f"\nDone. {inserted} inserted, {rejected} rejected, {deleted} old rows replaced.")

    except Exception as exc:
        conn.rollback()
        cur2 = conn.cursor()
        log_run_finish(cur2, run_id, "failed", error_message=str(exc))
        conn.commit()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
