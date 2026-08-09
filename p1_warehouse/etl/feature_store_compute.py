"""
Feature Store Computation
Computes features_weekly from surveillance_weekly + climate_weekly + habitat_suitability.
Run after all ETL sources are loaded.
Run: python p1_warehouse/etl/feature_store_compute.py [--year 2022] [--week 1]
"""
import os, math, argparse, psycopg2
from psycopg2.extras import execute_values
from datetime import date, datetime, timezone
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

# State adjacency (sharing a boundary) — for neighbour_cases_t1 feature
STATE_ADJACENCY = {
    "AB": ["IM", "AK", "CR", "EB", "AN"],
    "AD": ["GO", "BO", "TA", "BE"],
    "AK": ["CR", "RI", "AB"],
    "AN": ["IM", "AB", "EN", "DE"],
    "BA": ["GO", "JI", "KD", "NA", "PL"],
    "BY": ["RI", "DE", "IM"],
    "BE": ["NA", "KO", "KW", "NI", "PL", "TA", "AD"],
    "BO": ["YO", "AD", "GO"],
    "CR": ["AK", "AB", "EB", "EN"],
    "DE": ["ON", "ED", "AN", "IM", "BY", "RI"],
    "EB": ["AB", "AN", "EN", "CR"],
    "ED": ["OG", "OS", "KO", "ON", "DE", "RI"],
    "EK": ["KW", "OY", "OS", "ON"],
    "EN": ["AB", "EB", "AN", "KO", "BE"],
    "GO": ["BA", "AD", "BO", "BE"],
    "IM": ["AN", "AB", "AK", "BY", "DE"],
    "JI": ["KN", "KD", "BA"],
    "KD": ["KN", "ZA", "KT", "NI", "KW", "FC", "NA", "PL", "JI", "BA"],
    "KN": ["JI", "KD", "ZA", "KT"],
    "KT": ["ZA", "SO", "KN"],
    "KE": ["SO", "ZA", "NI"],
    "KO": ["FC", "NA", "BE", "KW", "OY", "OS", "ED", "ON", "EN"],
    "KW": ["OY", "EK", "OG", "NI", "KO", "FC", "BE", "KD"],
    "LA": ["OG"],
    "NA": ["FC", "KD", "PL", "BE", "KO", "BA"],
    "NI": ["SO", "KE", "ZA", "KD", "KW", "KO", "FC"],
    "OG": ["LA", "OY", "OS", "KW", "ED"],
    "ON": ["ED", "DE", "OS", "EK", "KO"],
    "OS": ["OY", "OG", "ED", "ON", "EK"],
    "OY": ["KW", "OG", "OS", "KO"],
    "PL": ["KD", "BA", "NA", "BE", "TA"],
    "RI": ["BY", "AK", "IM", "DE", "ED"],
    "SO": ["KE", "NI", "ZA", "KT"],
    "TA": ["AD", "BE", "PL"],
    "YO": ["BO"],
    "ZA": ["SO", "KE", "NI", "KD", "KN", "KT"],
    "FC": ["NI", "KD", "NA", "KO", "KW", "BE"],
}


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def build_state_lookup(cur):
    cur.execute("SELECT state_id, state_code, is_border_state FROM ref_states;")
    return {row[0]: {"code": row[1], "is_border": row[2]} for row in cur.fetchall()}


def build_code_to_id(state_info):
    return {v["code"]: k for k, v in state_info.items()}


def build_adjacency_ids(state_info, code_to_id):
    """Returns dict: state_id → [list of adjacent state_ids]"""
    adj = {}
    for sid, info in state_info.items():
        code = info["code"]
        neighbors = STATE_ADJACENCY.get(code, [])
        adj[sid] = [code_to_id[nc] for nc in neighbors if nc in code_to_id]
    return adj


def fetch_surveillance(cur, year=None, week=None):
    """Fetch all surveillance_weekly rows, optionally filtered."""
    sql = """SELECT state_id, epi_year, epi_week, confirmed, suspected, probable
             FROM surveillance_weekly"""
    params = []
    if year:
        sql += " WHERE epi_year = %s"
        params.append(year)
        if week:
            sql += " AND epi_week = %s"
            params.append(week)
    cur.execute(sql, params)
    rows = {}
    for state_id, yr, wk, confirmed, suspected, probable in cur.fetchall():
        c, s, p = confirmed or 0, suspected or 0, probable or 0
        rows[(state_id, yr, wk)] = {
            "confirmed": c,
            "suspected": s,
            "any_case":  c + s + p,
        }
    return rows


def fetch_climate(cur):
    """Fetch all climate_weekly rows."""
    cur.execute("""
        SELECT state_id, epi_year, epi_week, temp_mean_c, rainfall_mm, humidity_mean_pct
        FROM climate_weekly
    """)
    result = {}
    for row in cur.fetchall():
        sid, yr, wk, temp, rain, humid = row
        result[(sid, yr, wk)] = {"temp_mean": temp, "rainfall": rain, "humidity": humid}
    return result


def fetch_habitat(cur):
    """Fetch habitat suitability — use the most recent year available per state."""
    cur.execute("""
        SELECT DISTINCT ON (state_id) state_id, reservoir_risk_index
        FROM habitat_suitability
        WHERE spatial_unit = 'state' AND reservoir_risk_index IS NOT NULL
        ORDER BY state_id, year DESC
    """)
    return {row[0]: row[1] for row in cur.fetchall()}


def compute_features(surveillance, climate, habitat, state_info, adjacency_ids,
                     year_filter=None, week_filter=None):
    """
    Build the full feature matrix.
    Returns list of feature dicts, one per (state_id, epi_year, epi_week).
    """
    # Collect all (state_id, year, week) combinations from surveillance + climate
    all_keys = set(surveillance.keys()) | set(climate.keys())
    if year_filter:
        all_keys = {k for k in all_keys if k[1] == year_filter}
    if week_filter:
        all_keys = {k for k in all_keys if k[2] == week_filter}

    # Compute week_start_date helper
    def week_start(yr, wk):
        try:
            return date.fromisocalendar(yr, wk, 1)
        except ValueError:
            return date.fromisocalendar(yr, 52, 1)

    features = []
    for (state_id, yr, wk) in sorted(all_keys):
        # ── CASE LAG FEATURES ────────────────────────────────────────────────
        def cases(t_offset):
            yr2, wk2 = yr, wk - t_offset
            while wk2 <= 0:
                yr2 -= 1
                wk2 += 52
            return (surveillance.get((state_id, yr2, wk2)) or {}).get("any_case", 0)

        c_t1 = cases(1)
        c_t2 = cases(2)
        c_t4 = cases(4)

        # Rolling means (look back from t-1)
        roll4 = [cases(i) for i in range(1, 5)]
        roll8 = [cases(i) for i in range(1, 9)]
        roll4_mean = round(sum(roll4) / len(roll4), 2)
        roll8_mean = round(sum(roll8) / len(roll8), 2)
        log1p = round(math.log1p(c_t1), 4)

        # ── CLIMATE FEATURES ────────────────────────────────────────────────
        def clim(t_offset, field):
            yr2, wk2 = yr, wk - t_offset
            while wk2 <= 0:
                yr2 -= 1
                wk2 += 52
            return (climate.get((state_id, yr2, wk2)) or {}).get(field)

        rain_t2 = clim(2, "rainfall")
        rain_t4 = clim(4, "rainfall")
        temp_t1 = clim(1, "temp_mean")

        # ── RESERVOIR FEATURES ───────────────────────────────────────────────
        reservoir = habitat.get(state_id)

        # ── SPATIAL FEATURES ────────────────────────────────────────────────
        is_border = (state_info.get(state_id) or {}).get("is_border", False)

        # ── NEIGHBOUR CASES ─────────────────────────────────────────────────
        neighbours = adjacency_ids.get(state_id, [])
        neigh_cases = sum(
            (surveillance.get((n, yr, wk - 1)) or {}).get("any_case", 0)
            for n in neighbours
        )

        # ── TARGET VARIABLES ─────────────────────────────────────────────────
        future_yr = yr + 1 if wk + 4 > 52 else yr
        future_wk = wk + 4 - 52 if wk + 4 > 52 else wk + 4
        future_key = (state_id, future_yr, future_wk)
        future_data = surveillance.get(future_key)

        future_monday = week_start(future_yr, future_wk)
        today = date.today()

        if future_data is not None:
            # Surveillance record exists — any case (suspected/probable/confirmed) counts
            target_cases_4w = future_data.get("any_case", 0)
            target_4w = target_cases_4w > 0
        elif future_monday < today:
            # Week has passed with no sitrep → MCAR: no report = no detected case
            target_cases_4w = 0
            target_4w = False
        else:
            # Future week not yet reached — genuinely unknown
            target_cases_4w = None
            target_4w = None

        # ── COMPLETENESS CHECK ───────────────────────────────────────────────
        missing = []
        if temp_t1 is None: missing.append("temp_t1")
        if rain_t2 is None: missing.append("rainfall_t2")
        if reservoir is None: missing.append("reservoir_risk")
        is_complete = len(missing) == 0

        features.append({
            "state_id":             state_id,
            "epi_year":             yr,
            "epi_week":             wk,
            "week_start_date":      week_start(yr, wk),
            "cases_t1":             c_t1,
            "cases_t2":             c_t2,
            "cases_t4":             c_t4,
            "cases_rolling4w_mean": roll4_mean,
            "cases_rolling8w_mean": roll8_mean,
            "cases_log1p":          log1p,
            "rainfall_t2_mm":       rain_t2,
            "rainfall_t4_mm":       rain_t4,
            "temp_mean_t1_c":       temp_t1,
            "ndvi_t4_mean":         None,  # MODIS not yet loaded
            "ndvi_anomaly_t4":      None,
            "reservoir_risk_index": float(reservoir) if reservoir is not None else None,
            "forest_cover_pct":     None,  # GFW not yet loaded
            "deforestation_alert_cnt": None,
            "social_signal_zscore": None,  # P3 NLP not yet built
            "social_alert_flag":    False,
            "social_mpox_posts_t1": None,
            "is_border_state":      is_border,
            "healthcare_access_mean": None,
            "population_density":   None,
            "neighbour_cases_t1":   neigh_cases,
            "target_outbreak_4w":   target_4w,
            "target_cases_4w":      target_cases_4w,
            "is_complete":          is_complete,
            "missing_features":     missing if missing else None,
        })

    return features


FEATURE_COLS = (
    "state_id", "epi_year", "epi_week", "week_start_date",
    "cases_t1", "cases_t2", "cases_t4", "cases_rolling4w_mean", "cases_rolling8w_mean",
    "cases_log1p",
    "rainfall_t2_mm", "rainfall_t4_mm", "temp_mean_t1_c",
    "ndvi_t4_mean", "ndvi_anomaly_t4",
    "reservoir_risk_index", "forest_cover_pct", "deforestation_alert_cnt",
    "social_signal_zscore", "social_alert_flag", "social_mpox_posts_t1",
    "is_border_state", "healthcare_access_mean", "population_density",
    "neighbour_cases_t1",
    "target_outbreak_4w", "target_cases_4w",
    "is_complete", "missing_features",
)


def main():
    parser = argparse.ArgumentParser(description="Compute features_weekly from loaded ETL data")
    parser.add_argument("--year",  type=int, default=None, help="Limit to one year")
    parser.add_argument("--week",  type=int, default=None, help="Limit to one week")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=== Feature Store Computation ===")
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    state_info    = build_state_lookup(cur)
    code_to_id    = build_code_to_id(state_info)
    adjacency_ids = build_adjacency_ids(state_info, code_to_id)

    print("Loading source tables ...")
    surveillance = fetch_surveillance(cur, args.year, args.week)
    climate_data = fetch_climate(cur)
    habitat      = fetch_habitat(cur)
    print(f"  surveillance_weekly: {len(surveillance)} rows")
    print(f"  climate_weekly:      {len(climate_data)} rows")
    print(f"  habitat_suitability: {len(habitat)} rows")

    print("Computing features ...")
    features = compute_features(surveillance, climate_data, habitat, state_info,
                                adjacency_ids, args.year, args.week)
    complete_count = sum(1 for f in features if f["is_complete"])
    print(f"  Features computed: {len(features)} rows | {complete_count} complete")

    if args.dry_run:
        print("\n[DRY RUN] Sample features (first 3):")
        for f in features[:3]:
            print(f"  state={f['state_id']} {f['epi_year']}-W{f['epi_week']:02d} | "
                  f"cases_t1={f['cases_t1']} | temp={f['temp_mean_t1_c']} | "
                  f"rain={f['rainfall_t2_mm']} | reservoir={f['reservoir_risk_index']} | "
                  f"complete={f['is_complete']}")
        conn.rollback()
        return

    # Delete + replace for the affected scope
    if args.year:
        cur.execute("DELETE FROM features_weekly WHERE epi_year = %s", (args.year,))
    else:
        cur.execute("TRUNCATE TABLE features_weekly")
    deleted = cur.rowcount if not args.year else cur.rowcount

    # Batch insert
    values = [tuple(f[c] for c in FEATURE_COLS) for f in features]
    execute_values(cur, f"""
        INSERT INTO features_weekly ({",".join(FEATURE_COLS)}) VALUES %s
    """, values)

    conn.commit()
    print(f"\nDone. {len(features)} feature rows loaded into features_weekly.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
