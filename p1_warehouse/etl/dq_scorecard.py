"""
P1 Task 04 — Data Quality Scorecard
Computes completeness, coverage, timeliness, and consistency metrics
for each loaded data source and upserts into dq_scorecard.
Run: python p1_warehouse/etl/dq_scorecard.py
"""
import os, psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def run(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def upsert_score(cur, source_id, year, month, completeness, coverage,
                  consistency, consistency_against, timeliness, notes):
    overall = round(
        0.40 * (completeness or 0)
        + 0.30 * (coverage or 0)
        + 0.20 * (consistency or 0)
        + 0.10 * max(0, 100 - (timeliness or 0)),  # lower timeliness days → higher score
        2
    )
    # Clamp 0–100
    overall = max(0.0, min(100.0, overall))
    cur.execute("""
        INSERT INTO dq_scorecard
            (source_id, score_year, score_month, completeness_pct, timeliness_days_p50,
             consistency_pct, cross_validated_against, jurisdiction_coverage_pct,
             overall_score, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (source_id, score_year, score_month) DO UPDATE SET
            completeness_pct             = EXCLUDED.completeness_pct,
            timeliness_days_p50          = EXCLUDED.timeliness_days_p50,
            consistency_pct              = EXCLUDED.consistency_pct,
            cross_validated_against      = EXCLUDED.cross_validated_against,
            jurisdiction_coverage_pct    = EXCLUDED.jurisdiction_coverage_pct,
            overall_score                = EXCLUDED.overall_score,
            notes                        = EXCLUDED.notes,
            computed_at                  = NOW()
    """, (source_id, year, month, completeness, timeliness,
          consistency, consistency_against, coverage, overall, notes))


def score_ncdc_sitrep(cur, source_id, year, month):
    """Score the NCDC_SITREP source from surveillance_weekly."""
    # Total state-week records
    total = run(cur, "SELECT COUNT(*) FROM surveillance_weekly WHERE source_id = %s", (source_id,))
    if total == 0:
        return

    # Completeness: % of rows where at least one count is non-zero
    non_empty = run(cur, """
        SELECT COUNT(*) FROM surveillance_weekly
        WHERE source_id = %s AND (confirmed + suspected + probable) > 0
    """, (source_id,))
    completeness = round(non_empty / total * 100, 2) if total else 0

    # Coverage: % of 37 states with at least 1 record
    states_covered = run(cur, """
        SELECT COUNT(DISTINCT state_id) FROM surveillance_weekly WHERE source_id = %s
    """, (source_id,))
    coverage = round(states_covered / 37 * 100, 2)

    # Timeliness: NCDC sitrep PDFs are typically published ~1–2 weeks after epi week end
    timeliness = 10.5  # median 10.5 days (estimated from NCDC publication pattern)

    # Consistency: compare NCDC monthly totals against OWID
    cur.execute("""
        SELECT
            COUNT(*) AS months_compared,
            AVG(ABS(gap_owid_minus_ncdc::FLOAT / NULLIF(ncdc_sitrep_total, 0)) * 100) AS mean_abs_pct_diff
        FROM concordance_owid_ncdc
        WHERE ncdc_sitrep_total IS NOT NULL AND owid_total IS NOT NULL
          AND ncdc_sitrep_total > 0
    """)
    row = cur.fetchone()
    months_compared, mean_pct_diff = row if row else (0, None)
    consistency = round(100 - (mean_pct_diff or 100), 2) if mean_pct_diff is not None else None
    consistency = max(0.0, consistency) if consistency is not None else None

    notes = (f"Total state-week records: {total}; states covered: {states_covered}/37; "
             f"months cross-validated against OWID: {months_compared}")

    upsert_score(cur, source_id, year, month, completeness, coverage,
                 consistency, "OWID", timeliness, notes)
    print(f"  NCDC_SITREP: completeness={completeness}%, coverage={coverage}%, "
          f"consistency={consistency}%, timeliness=~{timeliness}d")


def score_owid(cur, source_id, year, month):
    """Score the OWID source from concordance_owid_ncdc."""
    cur.execute("SELECT COUNT(*), COUNT(owid_total) FROM concordance_owid_ncdc")
    total, non_null = cur.fetchone()
    if total == 0:
        return

    completeness = round(non_null / total * 100, 2) if total else 0

    # OWID is national-level only → coverage = 1/37 states (approx 2.7%)
    # But from a temporal standpoint, 48 months coverage is excellent
    months_covered = total
    coverage = round(min(months_covered / 48 * 100, 100), 2)  # 48 months (2020–2024)

    # Timeliness: OWID updates daily from GitHub CSV
    timeliness = 1.0

    # Consistency: mean absolute % gap vs NCDC
    cur.execute("""
        SELECT AVG(ABS(gap_owid_minus_ncdc::FLOAT / NULLIF(ncdc_sitrep_total, 0)) * 100)
        FROM concordance_owid_ncdc
        WHERE ncdc_sitrep_total IS NOT NULL AND ncdc_sitrep_total > 0
    """)
    mean_gap = cur.fetchone()[0]
    consistency = round(100 - (mean_gap or 100), 2) if mean_gap is not None else None
    consistency = max(0.0, consistency) if consistency is not None else None

    notes = f"National monthly totals: {total} months; temporal coverage: {months_covered} months"
    upsert_score(cur, source_id, year, month, completeness, coverage,
                 consistency, "NCDC_SITREP", timeliness, notes)
    print(f"  OWID: completeness={completeness}%, coverage={coverage}% (temporal), "
          f"consistency={consistency}%, timeliness=~{timeliness}d")


def score_global_health(cur, source_id, year, month):
    """Score the GLOBAL_HEALTH source from cases_individual."""
    total = run(cur, "SELECT COUNT(*) FROM cases_individual WHERE source_id = %s", (source_id,))
    if total == 0:
        print("  GLOBAL_HEALTH: no rows yet — skipping")
        return

    # Completeness: check 6 key fields
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE date_reported IS NOT NULL)   AS has_date,
            COUNT(*) FILTER (WHERE state_id IS NOT NULL)        AS has_state,
            COUNT(*) FILTER (WHERE age_group IS NOT NULL AND age_group != 'unknown') AS has_age,
            COUNT(*) FILTER (WHERE sex IN ('M','F'))             AS has_sex,
            COUNT(*) FILTER (WHERE case_classification IS NOT NULL) AS has_class,
            COUNT(*) FILTER (WHERE symptom_fever IS NOT NULL)   AS has_symptoms
        FROM cases_individual WHERE source_id = %s
    """, (source_id,))
    r = cur.fetchone()
    fields_avg = sum(r) / (6 * total) * 100
    completeness = round(fields_avg, 2)

    # Coverage: states represented
    states_covered = run(cur, """
        SELECT COUNT(DISTINCT state_id) FROM cases_individual WHERE source_id = %s AND state_id IS NOT NULL
    """, (source_id,))
    coverage = round(states_covered / 37 * 100, 2)

    # Timeliness: days from onset to confirmation (median)
    cur.execute("""
        SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_onset_to_report)
        FROM cases_individual
        WHERE source_id = %s AND days_onset_to_report IS NOT NULL AND days_onset_to_report > 0
    """, (source_id,))
    p50 = cur.fetchone()[0]
    timeliness = float(p50) if p50 else None

    notes = (f"Individual case records: {total}; states covered: {states_covered}/37; "
             f"median onset→report: {round(timeliness or 0, 1)}d")
    upsert_score(cur, source_id, year, month, completeness, coverage,
                 None, None, timeliness, notes)
    print(f"  GLOBAL_HEALTH: completeness={completeness}%, coverage={coverage}%, "
          f"timeliness={timeliness}d")


def score_gbif(cur, source_id, year, month):
    """Score the GBIF source from habitat_suitability."""
    total = run(cur, "SELECT COUNT(*) FROM habitat_suitability WHERE spatial_unit='state'")
    if total == 0:
        return

    non_null_cric = run(cur, """
        SELECT COUNT(*) FROM habitat_suitability
        WHERE spatial_unit='state' AND cricetomys_suit > 0
    """)
    completeness = round(non_null_cric / total * 100, 2)
    coverage = round(non_null_cric / 37 * 100, 2)
    timeliness = 30.0  # GBIF updates monthly

    notes = (f"States with Cricetomys records: {non_null_cric}/37; "
             f"density-proxy suitability model v1")
    upsert_score(cur, source_id, year, month, completeness, coverage,
                 None, None, timeliness, notes)
    print(f"  GBIF: completeness={completeness}%, coverage={coverage}%, timeliness=~{timeliness}d")


def backfill_concordance_ncdc(cur):
    """Aggregate surveillance_weekly into monthly totals and update concordance_owid_ncdc."""
    cur.execute("""
        UPDATE concordance_owid_ncdc c
        SET ncdc_sitrep_total = agg.monthly_total
        FROM (
            SELECT
                EXTRACT(YEAR  FROM week_start_date)::INT AS yr,
                EXTRACT(MONTH FROM week_start_date)::INT AS mo,
                SUM(confirmed + suspected + probable)    AS monthly_total
            FROM surveillance_weekly
            GROUP BY yr, mo
        ) agg
        WHERE c.year = agg.yr AND c.month = agg.mo
    """)
    updated = cur.rowcount
    print(f"  Concordance back-fill: {updated} months updated with NCDC totals")


def main():
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    print(f"=== Data Quality Scorecard — {year}-{month:02d} ===")

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    # Load source_id lookup
    cur.execute("SELECT source_code, source_id FROM ref_data_sources;")
    src = {row[0]: row[1] for row in cur.fetchall()}

    try:
        backfill_concordance_ncdc(cur)
        score_ncdc_sitrep(cur, src["NCDC_SITREP"], year, month)
        score_owid(cur, src["OWID"], year, month)
        score_global_health(cur, src["GLOBAL_HEALTH"], year, month)
        score_gbif(cur, src["GBIF"], year, month)
        conn.commit()
        print("\nDone. dq_scorecard updated.")

    except Exception as exc:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
