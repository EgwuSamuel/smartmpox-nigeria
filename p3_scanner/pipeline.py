"""
P3 Pipeline orchestrator.
Scrape → keyword filter → NLP analysis → store to Supabase social_media_signals.

Usage:
  python pipeline.py [--rss-only] [--reddit-only] [--dry-run]
"""

import os
import sys
import json
import argparse
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

# Sentry — activated only when SENTRY_DSN env var is set
try:
    import sentry_sdk
    _dsn = os.getenv("SENTRY_DSN")
    if _dsn:
        sentry_sdk.init(dsn=_dsn, traces_sample_rate=0.1)
        print("[Sentry] Initialised.")
except ImportError:
    pass

import pathlib
_ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

sys.path.insert(0, str(_ROOT))
from p3_scanner.scrapers.rss_scraper    import scrape_all as rss_scrape
from p3_scanner.scrapers.reddit_scraper import scrape_all as reddit_scrape
from p3_scanner.nlp.keyword_filter     import analyse

DB_URL = os.getenv("DATABASE_URL")

INSERT_SQL = """
INSERT INTO social_media_signals
    (platform, source_name, source_url, published_at, scraped_at,
     title, content_snippet, full_text,
     detected_language, is_mpox_relevant, relevance_score,
     keyword_matched, misinformation_flags, geo_mentions, state_id, sentiment)
VALUES
    (%(platform)s, %(source_name)s, %(source_url)s, %(published_at)s, %(scraped_at)s,
     %(title)s, %(content_snippet)s, %(full_text)s,
     %(detected_language)s, %(is_mpox_relevant)s, %(relevance_score)s,
     %(keyword_matched)s, %(misinformation_flags)s, %(geo_mentions)s, %(state_id)s, %(sentiment)s)
ON CONFLICT (platform, source_url) DO NOTHING
RETURNING id
"""


def run_pipeline(include_rss: bool = True, include_reddit: bool = True,
                 dry_run: bool = False, mpox_only: bool = False) -> dict:
    """
    Full pipeline run.
    Returns stats dict: {scraped, analysed, mpox_relevant, misinfo, inserted, skipped}.
    """
    stats = dict(scraped=0, analysed=0, mpox_relevant=0, misinfo=0, inserted=0, skipped=0)

    # ── 1. Scrape ────────────────────────────────────────────────
    articles = []
    if include_rss:
        print("\n[RSS] Scraping feeds...")
        articles.extend(rss_scrape())
    if include_reddit:
        print("\n[Reddit] Scraping subreddits...")
        articles.extend(reddit_scrape())

    stats["scraped"] = len(articles)
    print(f"\nTotal scraped: {len(articles)}")

    if not articles:
        print("Nothing scraped — exiting.")
        return stats

    # ── 2. NLP analysis ──────────────────────────────────────────
    print("\n[NLP] Analysing articles...")
    now = datetime.now(tz=timezone.utc).isoformat()
    enriched = []
    for art in articles:
        title = art.get("title", "") or ""
        body  = art.get("full_text", art.get("content_snippet", "")) or ""
        nlp   = analyse(title, body, forced_lang=art.get("forced_lang"))
        stats["analysed"] += 1
        if nlp["is_mpox_relevant"]:
            stats["mpox_relevant"] += 1
        if nlp["misinformation_flags"]:
            stats["misinfo"] += 1

        row = {
            "platform":          art.get("platform", "rss_news"),
            "source_name":       art.get("source_name"),
            "source_url":        art.get("source_url") or f"__no_url_{hash(title)}",
            "published_at":      art.get("published_at"),
            "scraped_at":        now,
            "title":             title[:500] if title else None,
            "content_snippet":   (art.get("content_snippet") or body)[:600],
            "full_text":         body[:4000],
            "detected_language": nlp["detected_language"],
            "is_mpox_relevant":  nlp["is_mpox_relevant"],
            "relevance_score":   nlp["relevance_score"],
            "keyword_matched":   nlp["keyword_matched"],
            "misinformation_flags": nlp["misinformation_flags"],
            "geo_mentions":      nlp["geo_mentions"],
            "state_id":          nlp["state_id"],
            "sentiment":         nlp["sentiment"],
        }
        # Filter to only mpox-relevant if requested
        if mpox_only and not nlp["is_mpox_relevant"]:
            continue
        enriched.append(row)

    print(f"  Analysed: {stats['analysed']}")
    print(f"  Mpox-relevant: {stats['mpox_relevant']}")
    print(f"  Misinformation flags: {stats['misinfo']}")
    print(f"  Rows to store: {len(enriched)}")

    if dry_run:
        print("\n[DRY RUN] Skipping DB insert.")
        print("Sample mpox-relevant items:")
        for r in [e for e in enriched if e["is_mpox_relevant"]][:5]:
            print(f"  [{r['source_name']}] {r['title'][:70]}")
            print(f"    keywords={r['keyword_matched']} misinfo={r['misinformation_flags']}")
        return stats

    # ── 3. Store to Supabase ──────────────────────────────────────
    print("\n[DB] Inserting rows...")
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()

    for row in enriched:
        try:
            cur.execute(INSERT_SQL, row)
            ret = cur.fetchone()
            if ret:
                stats["inserted"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            conn.rollback()
            stats["skipped"] += 1
            print(f"  [ERR] {e} — {row.get('source_url', '')[:60]}")
            continue
        conn.commit()

    cur.close()
    conn.close()

    print(f"\n=== Pipeline complete ===")
    print(f"  Scraped:     {stats['scraped']}")
    print(f"  Analysed:    {stats['analysed']}")
    print(f"  Mpox hits:   {stats['mpox_relevant']}")
    print(f"  Misinfo:     {stats['misinfo']}")
    print(f"  Inserted:    {stats['inserted']}")
    print(f"  Skipped/dup: {stats['skipped']}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P3 Scanner pipeline")
    parser.add_argument("--rss-only",    action="store_true")
    parser.add_argument("--reddit-only", action="store_true")
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--mpox-only",   action="store_true",
                        help="Only store mpox-relevant articles")
    args = parser.parse_args()

    include_rss    = not args.reddit_only
    include_reddit = not args.rss_only

    run_pipeline(
        include_rss=include_rss,
        include_reddit=include_reddit,
        dry_run=args.dry_run,
        mpox_only=args.mpox_only,
    )
