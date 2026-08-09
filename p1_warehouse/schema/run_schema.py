"""
Run all schema SQL files in order against the configured database.
Usage:  python p1_warehouse/schema/run_schema.py
"""
import os
import glob
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise EnvironmentError("DATABASE_URL not set in .env — copy .env.example to .env and fill it in.")

SCHEMA_DIR = Path(__file__).parent
SQL_FILES = sorted(glob.glob(str(SCHEMA_DIR / "0*.sql")))

def run_schema():
    print(f"Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    for sql_file in SQL_FILES:
        name = Path(sql_file).name
        print(f"  Running {name} ...", end=" ")
        with open(sql_file, "r", encoding="utf-8") as f:
            sql = f.read()
        try:
            cur.execute(sql)
            print("OK")
        except Exception as e:
            print(f"FAILED\n  Error: {e}")
            conn.close()
            raise

    cur.close()
    conn.close()
    print("\nSchema applied successfully.")

if __name__ == "__main__":
    run_schema()
