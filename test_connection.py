import psycopg2, os
from dotenv import load_dotenv

load_dotenv()
url = os.getenv("DATABASE_URL")
print("Connecting to:", url.split("@")[1])

conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute("SELECT count(*) FROM ref_data_sources;")
print("Connected OK. Rows in ref_data_sources:", cur.fetchone()[0])
conn.close()
