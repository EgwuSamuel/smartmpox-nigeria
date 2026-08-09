import os, psycopg2
from dotenv import load_dotenv
load_dotenv("C:/Users/USER/Desktop/SmartMpox/.env")

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()

with open("C:/Users/USER/Desktop/SmartMpox/p3_scanner/schema/009_social_signals.sql", encoding="utf-8") as f:
    sql = f.read()

cur.execute(sql)
conn.commit()
print("Migration 009 applied.")

cur.execute("SELECT COUNT(*) FROM social_media_signals")
print(f"Rows in social_media_signals: {cur.fetchone()[0]}")
cur.close(); conn.close()
