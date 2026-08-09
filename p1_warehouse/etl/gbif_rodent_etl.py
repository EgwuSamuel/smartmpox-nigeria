"""
ETL: GBIF Rodent Occurrences → habitat_suitability
Downloads Cricetomys gambianus and Funisciurus spp. occurrence records for Nigeria.
Computes log-normalized occurrence density per state as a suitability proxy.
Run: python p1_warehouse/etl/gbif_rodent_etl.py [--dry-run]
"""
import os, time, math, argparse, requests, psycopg2
from datetime import datetime, timezone
from collections import defaultdict
from dotenv import load_dotenv

GBIF_BASE = "https://api.gbif.org/v1"
HEADERS = {"User-Agent": "SmartMpox-Research/1.0 (academic; egwuonucheojosamuel@gmail.com)"}
REQUEST_DELAY = 1  # seconds between API calls

load_dotenv()

STATE_ALIASES = {
    "Abia": "AB", "Adamawa": "AD", "Akwa Ibom": "AK", "Akwa-Ibom": "AK",
    "Anambra": "AN", "Bauchi": "BA", "Bayelsa": "BY", "Benue": "BE",
    "Borno": "BO", "Cross River": "CR", "Cross-River": "CR", "Delta": "DE",
    "Ebonyi": "EB", "Edo": "ED", "Ekiti": "EK", "Enugu": "EN",
    "Gombe": "GO", "Imo": "IM", "Jigawa": "JI", "Kaduna": "KD",
    "Kano": "KN", "Katsina": "KT", "Kebbi": "KE", "Kogi": "KO",
    "Kwara": "KW", "Lagos": "LA", "Nasarawa": "NA", "Niger State": "NI",
    "Niger": "NI", "Ogun": "OG", "Ondo": "ON", "Osun": "OS", "Oyo": "OY",
    "Plateau": "PL", "Rivers": "RI", "Sokoto": "SO", "Taraba": "TA",
    "Yobe": "YO", "Zamfara": "ZA",
    "FCT": "FC", "Abuja": "FC", "Federal Capital Territory": "FC",
    # GBIF sometimes uses different formats
    "Lagos State": "LA", "Ogun State": "OG", "Edo State": "ED",
    "Rivers State": "RI", "Delta State": "DE", "Imo State": "IM",
}

# Approximate Nigeria state centroids (lat, lon) for coordinate-based fallback
STATE_CENTROIDS = {
    "AB": (5.45, 7.55), "AD": (9.33, 12.40), "AK": (4.90, 7.85),
    "AN": (6.22, 7.07), "BA": (10.31, 9.85), "BY": (4.77, 6.07),
    "BE": (7.34, 8.89), "BO": (11.85, 13.15), "CR": (5.87, 8.60),
    "DE": (5.68, 5.98), "EB": (6.32, 8.13), "ED": (6.33, 5.63),
    "EK": (7.72, 5.31), "EN": (6.46, 7.55), "GO": (10.29, 11.17),
    "IM": (5.49, 7.03), "JI": (12.22, 9.56), "KD": (10.60, 7.44),
    "KN": (11.99, 8.52), "KT": (12.99, 7.61), "KE": (11.50, 4.20),
    "KO": (7.80, 6.74), "KW": (8.80, 4.55), "LA": (6.52, 3.38),
    "NA": (8.56, 8.25), "NI": (9.93, 5.60), "OG": (6.99, 3.47),
    "ON": (7.25, 5.19), "OS": (7.56, 4.56), "OY": (7.85, 3.93),
    "PL": (9.22, 9.52), "RI": (4.82, 6.92), "SO": (13.06, 5.24),
    "TA": (7.99, 10.77), "YO": (12.30, 11.45), "ZA": (12.17, 6.23),
    "FC": (9.07, 7.39),
}


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def get_source_id(cur, code="GBIF"):
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


def lookup_species_key(name: str) -> int | None:
    """Return GBIF speciesKey for an exact species name."""
    resp = requests.get(f"{GBIF_BASE}/species/match",
                        params={"name": name, "strict": True},
                        headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    key = data.get("speciesKey") or data.get("usageKey")
    if key:
        print(f"  {name} → GBIF key {key}")
    return key


def lookup_genus_key(genus: str) -> int | None:
    """Return GBIF genusKey for a genus name."""
    resp = requests.get(f"{GBIF_BASE}/species/match",
                        params={"name": genus, "rank": "GENUS"},
                        headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    key = data.get("genusKey") or data.get("usageKey")
    if key:
        print(f"  Genus {genus} → GBIF key {key}")
    return key


def fetch_occurrences(taxon_key: int, taxon_param: str = "speciesKey",
                      country: str = "NG") -> list[dict]:
    """Paginate GBIF occurrence API for a species/genus in a country."""
    records = []
    limit = 300
    offset = 0
    while True:
        params = {taxon_param: taxon_key, "country": country,
                  "limit": limit, "offset": offset,
                  "hasCoordinate": True}
        resp = requests.get(f"{GBIF_BASE}/occurrence/search",
                            params=params, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("results", [])
        records.extend(batch)
        print(f"    Fetched {len(records)}/{data.get('count','?')} ...")
        if data.get("endOfRecords", True) or len(batch) == 0:
            break
        offset += limit
        time.sleep(REQUEST_DELAY)
    return records


def assign_state(rec: dict, state_lookup: dict) -> str | None:
    """Return state_code from stateProvince or nearest centroid fallback."""
    province = str(rec.get("stateProvince") or "").strip()
    if province:
        code = STATE_ALIASES.get(province)
        if code:
            return code

    # Coordinate-based fallback: find nearest centroid
    lat = rec.get("decimalLatitude")
    lon = rec.get("decimalLongitude")
    if lat is None or lon is None:
        return None
    best_code, best_dist = None, float("inf")
    for code, (clat, clon) in STATE_CENTROIDS.items():
        dist = math.sqrt((lat - clat) ** 2 + (lon - clon) ** 2)
        if dist < best_dist:
            best_dist, best_code = dist, code
    # Only accept if within ~3 degrees (~330 km) — keeps points inside Nigeria
    return best_code if best_dist < 3.0 else None


def compute_suitability(state_counts: dict[str, int]) -> dict[str, float]:
    """Log-normalize occurrence counts to 0–1 suitability score."""
    if not state_counts:
        return {}
    max_count = max(state_counts.values())
    if max_count == 0:
        return {k: 0.0 for k in state_counts}
    scores = {}
    for code, count in state_counts.items():
        if count == 0:
            scores[code] = 0.0
        else:
            # log1p normalises so zero → 0 and max → 1
            scores[code] = math.log1p(count) / math.log1p(max_count)
    return scores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=== GBIF Rodent Occurrence ETL ===")
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    source_id = get_source_id(cur)
    state_lookup = build_state_lookup(cur)  # code → id
    run_id = log_run_start(cur, source_id)
    conn.commit()

    year = datetime.now(timezone.utc).year

    try:
        # ── 1. Cricetomys gambianus ─────────────────────────────────────────
        print("\n[1/2] Cricetomys gambianus (giant pouched rat) ...")
        cric_key = lookup_species_key("Cricetomys gambianus")
        time.sleep(REQUEST_DELAY)

        cric_records = []
        if cric_key:
            cric_records = fetch_occurrences(cric_key, "speciesKey")
            print(f"  Total NGA records: {len(cric_records)}")
        else:
            print("  WARNING: species key not found; skipping Cricetomys.")

        # ── 2. Funisciurus spp. ────────────────────────────────────────────
        print("\n[2/2] Funisciurus spp. (rope squirrel) ...")
        fun_genus_key = lookup_genus_key("Funisciurus")
        time.sleep(REQUEST_DELAY)

        fun_records = []
        if fun_genus_key:
            fun_records = fetch_occurrences(fun_genus_key, "genusKey")
            print(f"  Total NGA records: {len(fun_records)}")
        else:
            print("  WARNING: genus key not found; skipping Funisciurus.")

        rows_fetched = len(cric_records) + len(fun_records)

        # ── 3. State-level counts ───────────────────────────────────────────
        cric_counts: dict[str, int] = defaultdict(int)
        fun_counts:  dict[str, int] = defaultdict(int)

        for rec in cric_records:
            code = assign_state(rec, state_lookup)
            if code:
                cric_counts[code] += 1

        for rec in fun_records:
            code = assign_state(rec, state_lookup)
            if code:
                fun_counts[code] += 1

        print(f"\nCricetomys — state coverage: {len(cric_counts)} states | "
              f"total assigned: {sum(cric_counts.values())}")
        print(f"Funisciurus  — state coverage: {len(fun_counts)} states | "
              f"total assigned: {sum(fun_counts.values())}")

        # ── 4. Suitability scores ───────────────────────────────────────────
        cric_suit = compute_suitability(cric_counts)
        fun_suit  = compute_suitability(fun_counts)

        # All 37 states get an entry (0.0 if no records)
        all_codes = set(state_lookup.keys())
        rows = []
        for code in all_codes:
            sid = state_lookup.get(code)
            if sid is None:
                continue
            c_score = round(cric_suit.get(code, 0.0), 4)
            f_score = round(fun_suit.get(code, 0.0),  4)
            rows.append({
                "state_id":         sid,
                "state_code":       code,
                "year":             year,
                "cricetomys_suit":  c_score,
                "funisciurus_suit": f_score,
                "gbif_record_count": cric_counts.get(code, 0) + fun_counts.get(code, 0),
            })

        if args.dry_run:
            print("\n[DRY RUN] Top-10 states by Cricetomys suitability:")
            top10 = sorted(rows, key=lambda r: r["cricetomys_suit"], reverse=True)[:10]
            for r in top10:
                print(f"  {r['state_code']} | cric={r['cricetomys_suit']:.3f} | "
                      f"fun={r['funisciurus_suit']:.3f} | n={r['gbif_record_count']}")
            conn.rollback()
            return

        # ── 5. Load into habitat_suitability (delete + replace) ───────────
        cur.execute("""
            DELETE FROM habitat_suitability WHERE spatial_unit = 'state' AND year = %s
        """, (year,))
        deleted = cur.rowcount

        inserted = rejected = 0
        for r in rows:
            try:
                cur.execute("SAVEPOINT sp_row")
                cur.execute("""
                    INSERT INTO habitat_suitability
                        (spatial_unit, state_id, year, cricetomys_suit,
                         funisciurus_suit, model_version, gbif_record_count)
                    VALUES ('state', %(state_id)s, %(year)s, %(cricetomys_suit)s,
                            %(funisciurus_suit)s, 'density-proxy-v1', %(gbif_record_count)s)
                """, r)
                cur.execute("RELEASE SAVEPOINT sp_row")
                inserted += 1
            except Exception as exc:
                cur.execute("ROLLBACK TO SAVEPOINT sp_row")
                rejected += 1
                print(f"  Skip {r['state_code']}: {exc}")

        log_run_finish(cur, run_id, "success",
                       rows_fetched=rows_fetched,
                       rows_inserted=inserted,
                       rows_rejected=rejected)
        conn.commit()
        print(f"\nDone. {inserted} inserted, {deleted} old rows replaced, {rejected} rejected.")
        print(f"  ({rows_fetched} GBIF records fetched, {len(rows)} state rows computed)")

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
