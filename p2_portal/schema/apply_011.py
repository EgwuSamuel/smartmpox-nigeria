import os, psycopg2
from dotenv import load_dotenv
load_dotenv("C:/Users/USER/Desktop/SmartMpox/.env")
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()
with open("C:/Users/USER/Desktop/SmartMpox/p2_portal/schema/011_admin_review.sql", encoding="utf-8") as f:
    cur.execute(f.read())
conn.commit()
print("Migration 011 applied.")
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='cases_individual' AND column_name LIKE 'review%'")
print("Review columns:", [r[0] for r in cur.fetchall()])
cur.close(); conn.close()
