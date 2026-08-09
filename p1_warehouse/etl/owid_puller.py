"""
ETL: OWID Mpox → concordance_owid_ncdc
Downloads Nigeria national case counts and upserts monthly totals.
Run: python p1_warehouse/etl/owid_puller.py
"""
import os, io, requests, psycopg2, pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv

OWID_URL = (
    "https://raw.githubusercontent.com/owid/monkeypox/main/owid-monkeypox-data.csv"
)

load_dotenv()


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def get_source_id(cur, code="OWID"):
    cur.execute("SELECT source_id FROM ref_data_sources WHERE source_code = %s", (code,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Source '{code}' not found in ref_data_sources. Run seed first.")
    return row[0]


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
            run_finished_at = %s,
            status          = %s,
            rows_fetched    = %s,
            rows_inserted   = %s,
            rows_updated    = %s,
            rows_rejected   = %s,
            error_message   = %s
        WHERE run_id = %s
    """, (datetime.now(timezone.utc), status, rows_fetched,
          rows_inserted, rows_updated, rows_rejected, error_message, run_id))


def fetch_owid() -> pd.DataFrame:
    print(f"Downloading OWID CSV from GitHub ...")
    resp = requests.get(OWID_URL, timeout=60)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    print(f"  Downloaded {len(df):,} rows across all countries.")

    # Filter Nigeria
    nga = df[df["iso_code"] == "NGA"].copy()
    print(f"  Nigeria rows: {len(nga):,}")
    if nga.empty:
        raise RuntimeError("No Nigeria rows in OWID CSV. Check iso_code filter.")

    nga["date"] = pd.to_datetime(nga["date"])
    nga["year"]  = nga["date"].dt.year
    nga["month"] = nga["date"].dt.month

    # Aggregate to monthly totals (sum new_cases per month)
    monthly = (
        nga.groupby(["year", "month"])
           .agg(owid_total=("new_cases", "sum"))
           .reset_index()
    )
    monthly["owid_total"] = monthly["owid_total"].fillna(0).astype(int)
    print(f"  Monthly buckets for Nigeria: {len(monthly)}")
    return monthly


def upsert_monthly(cur, monthly: pd.DataFrame):
    inserted = updated = 0
    for _, row in monthly.iterrows():
        cur.execute("""
            INSERT INTO concordance_owid_ncdc (year, month, owid_total)
            VALUES (%s, %s, %s)
            ON CONFLICT (year, month) DO UPDATE SET
                owid_total  = EXCLUDED.owid_total,
                computed_at = NOW()
            RETURNING (xmax = 0) AS was_inserted
        """, (int(row["year"]), int(row["month"]), int(row["owid_total"])))
        was_inserted = cur.fetchone()[0]
        if was_inserted:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


def main():
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    source_id = get_source_id(cur)
    run_id = log_run_start(cur, source_id)
    conn.commit()

    try:
        monthly = fetch_owid()
        rows_fetched = len(monthly)

        inserted, updated = upsert_monthly(cur, monthly)
        log_run_finish(cur, run_id, "success",
                       rows_fetched=rows_fetched,
                       rows_inserted=inserted,
                       rows_updated=updated)
        conn.commit()
        print(f"\nDone. {inserted} inserted, {updated} updated in concordance_owid_ncdc.")

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
