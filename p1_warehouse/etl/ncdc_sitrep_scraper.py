"""
ETL: NCDC Mpox Situation Report PDFs → surveillance_weekly
Fetches the monkeypox sitrep listing (cat=8), downloads each PDF,
extracts state-level case counts, and upserts into surveillance_weekly.

Run: python p1_warehouse/etl/ncdc_sitrep_scraper.py
     python p1_warehouse/etl/ncdc_sitrep_scraper.py --dry-run
     python p1_warehouse/etl/ncdc_sitrep_scraper.py --limit 5
"""
import os, re, io, time, argparse, logging, requests, pdfplumber, psycopg2
from datetime import datetime, timezone, date
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
MPOX_SITREP_URL = (
    "https://ncdc.gov.ng/diseases/sitreps/"
    "?cat=8&name=An%20Update%20of%20Monkeypox%20Outbreak%20in%20Nigeria"
)
HEADERS      = {"User-Agent": "SmartMpox-Research/1.0 (academic; egwuonucheojosamuel@gmail.com)"}
REQUEST_DELAY = 2
TIMEOUT       = 60

# ── State name → state_code mapping ───────────────────────────────────────
STATE_ALIASES: dict[str, str] = {
    "abia": "AB", "adamawa": "AD", "akwa ibom": "AK", "akwaibom": "AK",
    "anambra": "AN", "bauchi": "BA", "bayelsa": "BY", "benue": "BE",
    "borno": "BO", "cross river": "CR", "crossriver": "CR", "delta": "DE",
    "ebonyi": "EB", "edo": "ED", "ekiti": "EK", "enugu": "EN",
    "gombe": "GO", "imo": "IM", "jigawa": "JI", "kaduna": "KD",
    "kano": "KN", "katsina": "KT", "kebbi": "KE", "kogi": "KO",
    "kwara": "KW", "lagos": "LA", "nasarawa": "NA", "niger": "NI",
    "ogun": "OG", "ondo": "ON", "osun": "OS", "oyo": "OY",
    "plateau": "PL", "rivers": "RI", "river": "RI", "sokoto": "SO",
    "taraba": "TA", "yobe": "YO", "zamfara": "ZA",
    "fct": "FC", "fct abuja": "FC", "abuja": "FC",
    "federal capital territory": "FC",
    # sentinel — skip totals row
    "total": None, "nigeria": None, "national": None, "grand total": None,
}

SUSPECTED_COLS = {"suspected", "susp", "suspect"}
CONFIRMED_COLS = {"confirmed", "conf", "confirm"}
PROBABLE_COLS  = {"probable", "prob"}
DEATHS_COLS    = {"deaths", "death", "fatalities", "died"}
DISCARDED_COLS = {"discarded", "discard", "not a case"}


def normalize_state(raw: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", raw.strip().lower())
    cleaned = re.sub(r"[^a-z ]", "", cleaned).strip()
    return STATE_ALIASES.get(cleaned)


def col_index(headers: list[str], candidates: set[str]) -> int | None:
    for i, h in enumerate(headers):
        if any(c in h.lower() for c in candidates):
            return i
    return None


def safe_int(val) -> int:
    if val is None:
        return 0
    try:
        return int(str(val).strip().replace(",", "") or 0)
    except (ValueError, TypeError):
        return 0


def week_start(year: int, week: int) -> date:
    try:
        return date.fromisocalendar(year, week, 1)
    except ValueError:
        return date.fromisocalendar(year, 52, 1)  # week 53 only exists in some years


# ── Page scraping ──────────────────────────────────────────────────────────

def fetch_sitrep_links() -> list[dict]:
    """
    Fetches the NCDC mpox sitrep listing page and returns:
      [{url, title, week}]   (year extracted later from PDF)
    """
    log.info(f"Fetching NCDC monkeypox sitrep index ...")
    resp = requests.get(MPOX_SITREP_URL, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    links = []
    seen = set()

    # Primary structure: <table class="table"> with rows SN | Title | PDF link
    for table in soup.find_all("table", class_="table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            title_text = tds[1].get_text(" ", strip=True) if len(tds) > 1 else ""
            a_tag = tr.find("a", href=re.compile(r"\.pdf", re.I))
            if not a_tag:
                continue
            href = a_tag["href"]
            if not href.startswith("http"):
                href = "https://ncdc.gov.ng" + href

            if href in seen:
                continue
            seen.add(href)

            week = _extract_week(title_text + " " + href)
            links.append({"url": href, "title": title_text, "week": week})

    # Fallback: any PDF link on the page
    if not links:
        log.warning("Table parse found 0 links — falling back to all-PDF scan.")
        for a in soup.find_all("a", href=re.compile(r"\.pdf", re.I)):
            href = a["href"]
            if not href.startswith("http"):
                href = "https://ncdc.gov.ng" + href
            if href in seen:
                continue
            seen.add(href)
            title = a.get_text(" ", strip=True)
            links.append({"url": href, "title": title,
                          "week": _extract_week(title + " " + href)})

    log.info(f"Found {len(links)} PDF links.")
    return links


def _extract_week(text: str) -> int | None:
    """Extract epi week number from title text."""
    m = re.search(r"[Ww]eek\s*(\d{1,2})", text)
    if m:
        w = int(m.group(1))
        return w if 1 <= w <= 53 else None
    return None


# ── Year extraction from PDF first page ───────────────────────────────────

def extract_year_from_pdf(pdf_bytes: bytes) -> int | None:
    """
    Reads up to the first 2 pages looking for a 4-digit year near 'week' or a date.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:2]:
                text = page.extract_text() or ""
                m = re.search(r"[Ww]eek\s*\d{1,2}[,\s]+(\d{4})", text)
                if m:
                    y = int(m.group(1))
                    if 2017 <= y <= 2030:
                        return y
                for m in re.finditer(r"\b(20\d{2})\b", text):
                    y = int(m.group(1))
                    if 2017 <= y <= 2030:
                        return y
    except Exception as exc:
        log.warning(f"  PDF parse error (year extraction): {exc}")
    return None


# ── PDF parsing ────────────────────────────────────────────────────────────

def parse_pdf(pdf_bytes: bytes) -> list[dict]:
    """
    Tries multiple strategies in order of richness.
    Returns list of {state_code, suspected, probable, confirmed, discarded, deaths}.
    """
    # Gather all text up front (fast; used by text strategies)
    all_text = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = list(pdf.pages)
            for page in pages:
                all_text += (page.extract_text() or "") + "\n"

            # Strategy 1: structured state table in any page
            for page_num, page in enumerate(pages, 1):
                rows = _try_table_extraction(page)
                if rows:
                    log.debug(f"  Strategy 1 (table) from page {page_num}: {len(rows)} states")
                    return rows
    except Exception as exc:
        log.warning(f"  PDF parse error (table extraction): {exc}")
        # Fall through to text strategies with whatever text was collected

    # Strategy 2 (2024 format): "Confirmed (N): Lagos 3, Ogun 1, Delta 1, Rivers 1"
    rows = _extract_confirmed_text_breakdown(all_text)
    if rows:
        log.debug(f"  Strategy 2 (2024 confirmed text): {len(rows)} states")
        return rows

    # Strategy 3 (2023 format): "FCT (5), Lagos (4), Ondo (3), ..." in highlights
    rows = _extract_suspected_text_breakdown(all_text)
    if rows:
        log.debug(f"  Strategy 3 (2023 suspected highlights): {len(rows)} states")
        return rows

    return []


def _try_table_extraction(page) -> list[dict]:
    for table in page.extract_tables():
        if not table or len(table) < 5:
            continue
        header_row_idx = None
        for i, row in enumerate(table):
            if row and any(
                cell and any(kw in str(cell).lower()
                             for kw in ["state", "suspect", "confirm", "prob", "death"])
                for cell in row
            ):
                header_row_idx = i
                break
        if header_row_idx is None:
            continue

        headers   = [str(c or "").strip().lower() for c in table[header_row_idx]]
        state_col = next((i for i, h in enumerate(headers)
                          if "state" in h or "jurisd" in h), 0)
        susp_col  = col_index(headers, SUSPECTED_COLS)
        conf_col  = col_index(headers, CONFIRMED_COLS)
        prob_col  = col_index(headers, PROBABLE_COLS)
        dths_col  = col_index(headers, DEATHS_COLS)
        disc_col  = col_index(headers, DISCARDED_COLS)

        if conf_col is None and susp_col is None:
            continue

        parsed = []
        for row in table[header_row_idx + 1:]:
            if not row or not row[state_col]:
                continue
            code = normalize_state(str(row[state_col]))
            if code is None:
                continue
            parsed.append({
                "state_code": code,
                "suspected":  safe_int(row[susp_col]  if susp_col  is not None else None),
                "probable":   safe_int(row[prob_col]  if prob_col  is not None else None),
                "confirmed":  safe_int(row[conf_col]  if conf_col  is not None else None),
                "discarded":  safe_int(row[disc_col]  if disc_col  is not None else None),
                "deaths":     safe_int(row[dths_col]  if dths_col  is not None else None),
            })
        if len(parsed) >= 5:
            return parsed
    return []


def _extract_confirmed_text_breakdown(text: str) -> list[dict]:
    """
    2024 format: "Confirmed (6): Lagos 3, Ogun 1, Delta 1, Rivers 1"
    Returns rows only for states with confirmed > 0.
    """
    m = re.search(
        r"Confirmed\s*\(\d+\)\s*[:\-]\s*([^\n\.]+)",
        text, re.IGNORECASE
    )
    if not m:
        return []
    segment = m.group(1)
    # Parse "State N, State N and State N"
    pairs = re.findall(r"([A-Za-z][A-Za-z\s]{1,20}?)\s+(\d+)(?:[,\s]|$)", segment)
    rows = []
    for state_raw, count in pairs:
        code = normalize_state(state_raw)
        if code:
            rows.append({
                "state_code": code,
                "suspected":  0,
                "probable":   0,
                "confirmed":  safe_int(count),
                "discarded":  0,
                "deaths":     0,
            })

    # Also pull national suspected total and attach to first row (informational)
    susp_m = re.search(r"Suspected Cases?\s*[:\-]\s*(\d+)", text, re.IGNORECASE)
    if susp_m and rows:
        # Store national suspected total in a sentinel row (state_code=None filtered out)
        pass  # national total goes to concordance table, not per-state rows

    return rows if rows else []


def _extract_suspected_text_breakdown(text: str) -> list[dict]:
    """
    2023 format highlights: "FCT (5), Lagos (4), Ondo (3), Oyo (3), Abia (2), ..."
    Found after "States and FCT –" or "States –".
    """
    # Find the list of states with bracket counts
    m = re.search(
        r"(?:States?\s+and\s+FCT|FCT\s+and\s+States?)\s*[–\-]\s*([^\n]{20,})"
        r"|(?:reported\s+from[^–\-]*[–\-])\s*([^\n]{20,})",
        text, re.IGNORECASE
    )
    segment = ""
    if m:
        segment = m.group(1) or m.group(2) or ""

    if not segment:
        # Fallback: any "Word (N)" pattern cluster
        clusters = re.findall(
            r"(?:[A-Z][A-Za-z\s]{1,20}\s*\(\d+\)[,\s]*){3,}",
            text
        )
        if clusters:
            segment = clusters[0]

    if not segment:
        return []

    pairs = re.findall(r"([A-Za-z][A-Za-z\s]{1,20}?)\s*\((\d+)\)", segment)
    rows = []
    for state_raw, count in pairs:
        code = normalize_state(state_raw)
        if code:
            rows.append({
                "state_code": code,
                "suspected":  safe_int(count),
                "probable":   0,
                "confirmed":  0,
                "discarded":  0,
                "deaths":     0,
            })
    return rows if rows else []


# ── Database helpers ───────────────────────────────────────────────────────

def get_source_id(cur, code="NCDC_SITREP"):
    cur.execute("SELECT source_id FROM ref_data_sources WHERE source_code=%s", (code,))
    return cur.fetchone()[0]


def get_state_map(cur) -> dict[str, int]:
    cur.execute("SELECT state_code, state_id FROM ref_states")
    return {r[0]: r[1] for r in cur.fetchall()}


def log_run_start(cur, source_id):
    cur.execute("""
        INSERT INTO etl_run_log (source_id, run_started_at, status)
        VALUES (%s, %s, 'running') RETURNING run_id
    """, (source_id, datetime.now(timezone.utc)))
    return cur.fetchone()[0]


def log_run_finish(cur, run_id, status, rows_fetched=0, rows_inserted=0,
                   rows_updated=0, rows_rejected=0, error_message=None):
    cur.execute("""
        UPDATE etl_run_log SET
            run_finished_at=%s, status=%s, rows_fetched=%s,
            rows_inserted=%s, rows_updated=%s, rows_rejected=%s,
            error_message=%s
        WHERE run_id=%s
    """, (datetime.now(timezone.utc), status, rows_fetched,
          rows_inserted, rows_updated, rows_rejected, error_message, run_id))


def upsert_week(cur, state_id, year, week, row, source_id) -> str:
    wstart = week_start(year, week)
    cur.execute("""
        INSERT INTO surveillance_weekly
            (state_id, epi_year, epi_week, week_start_date,
             suspected, probable, confirmed, discarded, deaths,
             source_id, source_confirmed)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (state_id, epi_year, epi_week) DO UPDATE SET
            suspected        = EXCLUDED.suspected,
            probable         = EXCLUDED.probable,
            confirmed        = EXCLUDED.confirmed,
            discarded        = EXCLUDED.discarded,
            deaths           = EXCLUDED.deaths,
            source_id        = EXCLUDED.source_id,
            source_confirmed = EXCLUDED.confirmed,
            updated_at       = NOW()
        RETURNING (xmax = 0) AS was_inserted
    """, (state_id, year, week, wstart,
          row["suspected"], row["probable"], row["confirmed"],
          row["discarded"], row["deaths"], source_id, row["confirmed"]))
    return "inserted" if cur.fetchone()[0] else "updated"


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process at most N PDFs (0 = all)")
    args = parser.parse_args()

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    conn.autocommit = False
    cur = conn.cursor()

    source_id = get_source_id(cur)
    state_map = get_state_map(cur)
    run_id    = log_run_start(cur, source_id)
    conn.commit()

    links = fetch_sitrep_links()
    if args.limit:
        links = links[:args.limit]

    total_fetched = total_inserted = total_updated = total_rejected = 0
    year_failures = []
    parse_failures = []

    for idx, link in enumerate(links, 1):
        url   = link["url"]
        week  = link["week"]
        title = link["title"]

        log.info(f"[{idx}/{len(links)}] {title[:60]}  week={week}")

        if week is None:
            log.warning("  No week found in title — skipping.")
            total_rejected += 1
            continue

        # Download PDF
        try:
            time.sleep(REQUEST_DELAY)
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            pdf_bytes = resp.content
        except Exception as exc:
            log.warning(f"  Download failed: {exc}")
            parse_failures.append(url)
            total_rejected += 1
            continue

        # Extract year from PDF
        year = extract_year_from_pdf(pdf_bytes)
        if year is None:
            log.warning(f"  Could not determine year from PDF.")
            year_failures.append(url)
            total_rejected += 1
            continue

        log.info(f"  Year={year}, Week={week}")

        # Parse state table
        rows = parse_pdf(pdf_bytes)
        if not rows:
            log.warning(f"  No state table found in PDF.")
            parse_failures.append(url)
            total_rejected += 1
            continue

        log.info(f"  Parsed {len(rows)} state rows.")
        total_fetched += len(rows)

        if args.dry_run:
            for r in rows[:3]:
                log.info(f"    DRY-RUN: year={year} week={week} {r}")
            continue

        # Write to DB
        for row in rows:
            sid = state_map.get(row["state_code"])
            if sid is None:
                log.warning(f"  Unknown state code: {row['state_code']}")
                total_rejected += 1
                continue
            try:
                result = upsert_week(cur, sid, year, week, row, source_id)
                if result == "inserted":
                    total_inserted += 1
                else:
                    total_updated += 1
            except Exception as exc:
                log.error(f"  DB error: {exc}")
                total_rejected += 1

        conn.commit()

    status = "success" if not (parse_failures + year_failures) else (
        "partial" if total_inserted + total_updated > 0 else "failed"
    )
    cur2 = conn.cursor()
    log_run_finish(cur2, run_id, status,
                   rows_fetched=total_fetched,
                   rows_inserted=total_inserted,
                   rows_updated=total_updated,
                   rows_rejected=total_rejected)
    conn.commit()
    cur.close(); cur2.close(); conn.close()

    print(f"\n{'='*50}")
    print(f"Status   : {status}")
    print(f"PDFs     : {len(links)} found")
    print(f"Rows     : {total_inserted} inserted, {total_updated} updated, "
          f"{total_rejected} rejected")
    if year_failures:
        print(f"No year  : {len(year_failures)} PDFs")
    if parse_failures:
        print(f"No table : {len(parse_failures)} PDFs")


if __name__ == "__main__":
    main()
