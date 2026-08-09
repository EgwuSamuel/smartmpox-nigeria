"""
ETL: Open-Meteo Historical Climate → climate_weekly
Uses the Open-Meteo archive API (ERA5/ERA5-Land reanalysis, no API key required).
Populates climate_weekly with weekly temperature, rainfall, and humidity per state.
Run: python p1_warehouse/etl/openmeteo_climate_etl.py [--state FC] [--year 2022]
"""
import os, time, argparse, requests, psycopg2
from datetime import date as _date
from psycopg2.extras import execute_values
from datetime import date
from dotenv import load_dotenv

load_dotenv()

# Open-Meteo historical archive (ERA5 reanalysis, free, no auth)
OPENMETEO_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_DELAY = 1  # seconds between API calls

# Nigerian state capitals with precise coordinates
STATE_CAPITALS = {
    "AB": (5.532,  7.486,  "Umuahia"),
    "AD": (9.204,  12.495, "Yola"),
    "AK": (5.053,  7.924,  "Uyo"),
    "AN": (6.211,  7.068,  "Awka"),
    "BA": (10.306, 9.850,  "Bauchi"),
    "BY": (4.765,  6.069,  "Yenagoa"),
    "BE": (7.341,  8.891,  "Makurdi"),
    "BO": (11.835, 13.151, "Maiduguri"),
    "CR": (4.950,  8.323,  "Calabar"),
    "DE": (5.681,  5.981,  "Asaba"),
    "EB": (6.326,  8.129,  "Abakaliki"),
    "ED": (6.335,  5.627,  "Benin City"),
    "EK": (7.718,  5.311,  "Ado-Ekiti"),
    "EN": (6.442,  7.498,  "Enugu"),
    "GO": (10.290, 11.170, "Gombe"),
    "IM": (5.492,  7.026,  "Owerri"),
    "JI": (12.228, 9.558,  "Dutse"),
    "KD": (10.520, 7.440,  "Kaduna"),
    "KN": (11.996, 8.517,  "Kano"),
    "KT": (12.990, 7.611,  "Katsina"),
    "KE": (12.452, 4.200,  "Birnin Kebbi"),
    "KO": (7.804,  6.741,  "Lokoja"),
    "KW": (8.490,  4.550,  "Ilorin"),
    "LA": (6.524,  3.379,  "Lagos"),
    "NA": (8.557,  8.250,  "Lafia"),
    "NI": (9.913,  5.599,  "Minna"),
    "OG": (6.998,  3.472,  "Abeokuta"),
    "ON": (7.250,  5.195,  "Akure"),
    "OS": (7.557,  4.555,  "Osogbo"),
    "OY": (7.850,  3.930,  "Ibadan"),
    "PL": (9.217,  9.517,  "Jos"),
    "RI": (4.817,  7.050,  "Port Harcourt"),
    "SO": (13.060, 5.240,  "Sokoto"),
    "TA": (7.993,  10.773, "Jalingo"),
    "YO": (11.746, 11.962, "Damaturu"),
    "ZA": (12.172, 6.232,  "Gusau"),
    "FC": (9.072,  7.491,  "Abuja"),
}


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def build_state_lookup(cur):
    cur.execute("SELECT state_id, state_code FROM ref_states;")
    return {row[1]: row[0] for row in cur.fetchall()}


def get_source_id(cur, code="ERA5"):
    cur.execute("SELECT source_id FROM ref_data_sources WHERE source_code = %s", (code,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Source '{code}' not in ref_data_sources. Run seed first.")
    return row[0]


def iso_week_start(year: int, week: int) -> date:
    try:
        return date.fromisocalendar(year, week, 1)
    except ValueError:
        return date.fromisocalendar(year, 52, 1)


def fetch_climate(lat: float, lon: float, start: str, end: str) -> dict:
    """Fetch daily climate for a point from Open-Meteo. Returns dict of date→values."""
    params = {
        "latitude":    lat,
        "longitude":   lon,
        "start_date":  start,
        "end_date":    end,
        "daily":       "temperature_2m_mean,temperature_2m_max,temperature_2m_min,"
                       "precipitation_sum,relative_humidity_2m_mean",
        "timezone":    "Africa/Lagos",
    }
    resp = requests.get(OPENMETEO_URL, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    daily = data.get("daily", {})
    result = {}
    for i, dt_str in enumerate(daily.get("time", [])):
        result[dt_str] = {
            "temp_mean":  daily["temperature_2m_mean"][i],
            "temp_max":   daily["temperature_2m_max"][i],
            "temp_min":   daily["temperature_2m_min"][i],
            "rainfall":   daily["precipitation_sum"][i],
            "humidity":   daily.get("relative_humidity_2m_mean", [None] * (i+1))[i],
        }
    return result


def aggregate_to_weekly(daily_data: dict, year: int) -> list[dict]:
    """Aggregate daily data to ISO epi-week means for a given year."""
    from collections import defaultdict

    week_buckets = defaultdict(list)
    for dt_str, vals in daily_data.items():
        d = date.fromisoformat(dt_str)
        iso_year, iso_week, _ = d.isocalendar()
        if iso_year == year:
            week_buckets[iso_week].append(vals)

    rows = []
    for week, day_vals in sorted(week_buckets.items()):
        def avg(key):
            vals = [v[key] for v in day_vals if v[key] is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        rows.append({
            "epi_year":   year,
            "epi_week":   week,
            "week_start": iso_week_start(year, week),
            "temp_mean":  avg("temp_mean"),
            "temp_max":   avg("temp_max"),
            "temp_min":   avg("temp_min"),
            "rainfall":   round(sum(v["rainfall"] for v in day_vals if v["rainfall"] is not None), 2),
            "humidity":   avg("humidity"),
        })
    return rows


def upsert_climate_rows(cur, state_id: int, source_id: int, rows: list[dict]) -> tuple[int, int]:
    values = [
        (state_id, r["epi_year"], r["epi_week"], r["week_start"],
         r["temp_mean"], r["temp_max"], r["temp_min"], r["rainfall"], r["humidity"])
        for r in rows
    ]
    execute_values(cur, """
        INSERT INTO climate_weekly
            (state_id, epi_year, epi_week, week_start_date,
             temp_mean_c, temp_max_c, temp_min_c, rainfall_mm, humidity_mean_pct, source)
        VALUES %s
        ON CONFLICT (state_id, epi_year, epi_week) DO UPDATE SET
            temp_mean_c       = EXCLUDED.temp_mean_c,
            temp_max_c        = EXCLUDED.temp_max_c,
            temp_min_c        = EXCLUDED.temp_min_c,
            rainfall_mm       = EXCLUDED.rainfall_mm,
            humidity_mean_pct = EXCLUDED.humidity_mean_pct,
            ingested_at       = NOW()
    """, values, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,'ERA5')")
    return len(values), 0  # can't distinguish insert vs update with execute_values easily


def main():
    parser = argparse.ArgumentParser(description="ERA5 climate via Open-Meteo → climate_weekly")
    parser.add_argument("--state", default=None, help="Single state code (e.g. FC). Default: all 37.")
    parser.add_argument("--year",  type=int, default=None,
                        help="Single year. Default: 2017–2024.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    years = [args.year] if args.year else list(range(2017, _date.today().year + 1))
    states = ([args.state] if args.state else list(STATE_CAPITALS.keys()))

    print(f"=== Open-Meteo Climate ETL ===")
    print(f"  States: {len(states)}  |  Years: {years}")

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    state_lookup = build_state_lookup(cur)
    source_id    = get_source_id(cur)

    total_inserted = total_updated = 0

    for code in states:
        state_id = state_lookup.get(code)
        if state_id is None:
            print(f"  [{code}] Not found in ref_states — skipping")
            continue

        lat, lon, capital = STATE_CAPITALS[code]
        start = f"{min(years)}-01-01"
        end   = f"{max(years)}-12-31"

        print(f"  [{code}] {capital} ({lat},{lon}) ...")
        try:
            daily = fetch_climate(lat, lon, start, end)
        except Exception as exc:
            print(f"    API error: {exc}")
            time.sleep(REQUEST_DELAY * 2)
            continue

        all_rows = []
        for yr in years:
            all_rows.extend(aggregate_to_weekly(daily, yr))
        print(f"    → {len(all_rows)} weekly rows")

        if not args.dry_run:
            ins, upd = upsert_climate_rows(cur, state_id, source_id, all_rows)
            total_inserted += ins
            total_updated  += upd
            conn.commit()
            print(f"    → {ins} upserted")

        time.sleep(REQUEST_DELAY)

    if not args.dry_run:
        print(f"\nDone. {total_inserted} inserted, {total_updated} updated in climate_weekly.")
    else:
        print(f"\n[DRY RUN] Done. {sum(1 for c in states if c in STATE_CAPITALS)} states processed.")
    conn.close()


if __name__ == "__main__":
    main()
