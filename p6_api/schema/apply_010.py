import os, psycopg2
from dotenv import load_dotenv
load_dotenv("C:/Users/USER/Desktop/SmartMpox/.env")

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()
with open("C:/Users/USER/Desktop/SmartMpox/p6_api/schema/010_cross_border_alerts.sql", encoding="utf-8") as f:
    sql = f.read()
cur.execute(sql)
conn.commit()
print("Migration 010 applied.")
cur.execute("SELECT COUNT(*) FROM cross_border_alerts")
print(f"cross_border_alerts rows: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM api_latest_risk")
print(f"api_latest_risk rows: {cur.fetchone()[0]}")
cur.close(); conn.close()
