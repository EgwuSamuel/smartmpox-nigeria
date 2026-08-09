"""
Debug: show exactly what pdfplumber sees in the first NCDC mpox sitrep PDF.
Run: python p1_warehouse/etl/debug_pdf.py
"""
import io, requests, pdfplumber

URL = ("https://ncdc.gov.ng/themes/common/files/sitreps/"
       "e6acbf046b333e69bedcea6983240e4e.pdf")  # SN 60 mid-era
HEADERS = {"User-Agent": "SmartMpox-Research/1.0 (academic; egwuonucheojosamuel@gmail.com)"}

print("Downloading PDF ...")
resp = requests.get(URL, headers=HEADERS, timeout=60)
pdf_bytes = resp.content
print(f"  Size: {len(pdf_bytes):,} bytes\n")

with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
    print(f"Total pages: {len(pdf.pages)}\n")
    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        tables = page.extract_tables()
        print(f"{'='*60}")
        print(f"PAGE {i}  |  tables found: {len(tables)}")
        print(f"--- TEXT (first 800 chars) ---")
        print(text[:800])
        print()
        for j, tbl in enumerate(tables):
            print(f"  --- Table {j+1}: {len(tbl)} rows x {len(tbl[0]) if tbl else 0} cols ---")
            for row in tbl[:6]:
                print(f"    {row}")
        print()
