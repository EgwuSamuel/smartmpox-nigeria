"""
Export 200 articles from social_media_signals for manual annotation.
Saves annotation_sample.csv to the project root.

Run:
    python p3_scanner/export_annotation_sample.py
"""

import os
import csv
import pathlib
import psycopg2
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

OUT = pathlib.Path(__file__).resolve().parent.parent / "annotation_sample.csv"

QUERY = """
    (
        SELECT id, platform, published_at, detected_language,
               title, content_snippet, source_url, is_mpox_relevant
        FROM social_media_signals
        WHERE is_mpox_relevant = TRUE
          AND title IS NOT NULL
        ORDER BY RANDOM()
        LIMIT 100
    )
    UNION ALL
    (
        SELECT id, platform, published_at, detected_language,
               title, content_snippet, source_url, is_mpox_relevant
        FROM social_media_signals
        WHERE is_mpox_relevant = FALSE
          AND title IS NOT NULL
        ORDER BY RANDOM()
        LIMIT 100
    )
"""

def main():
    print("Connecting to database...")
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur  = conn.cursor()

    print("Fetching 200 articles (100 relevant + 100 irrelevant)...")
    cur.execute(QUERY)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]

    cur.close()
    conn.close()

    if not rows:
        print("ERROR: No rows returned. Check social_media_signals table.")
        return

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Header: model_label = what the keyword filter decided
        #         human_label = blank column for you to fill (1 or 0)
        writer.writerow([
            "id", "platform", "published_at", "language",
            "title", "snippet", "source_url",
            "model_label", "human_label"
        ])
        for row in rows:
            d = dict(zip(cols, row))
            writer.writerow([
                d["id"],
                d["platform"],
                d["published_at"],
                d["detected_language"],
                d["title"],
                (d["content_snippet"] or "")[:300],
                d["source_url"],
                1 if d["is_mpox_relevant"] else 0,
                ""   # ← YOU FILL THIS IN
            ])

    total = len(rows)
    pos   = sum(1 for r in rows if dict(zip(cols, r))["is_mpox_relevant"])
    neg   = total - pos

    print(f"\nDone.")
    print(f"  Rows exported : {total}  ({pos} relevant, {neg} irrelevant)")
    print(f"  Saved to      : {OUT}")
    print(f"\nNext step:")
    print(f"  Open annotation_sample.csv in Excel.")
    print(f"  Fill the 'human_label' column with 1 (mpox) or 0 (not mpox).")
    print(f"  Save when done.")

if __name__ == "__main__":
    main()
