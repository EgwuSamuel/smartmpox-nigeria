"""
One-time setup: download + simplify Nigeria state boundaries from GADM.
Saves to docs/dashboard/nigeria_states.geojson for the Leaflet choropleth map.

Run once:
  pip install requests
  python get_nigeria_geojson.py
"""

import json
import math
import os
import requests

GADM_URL   = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_NGA_1.json"
OUT_PATHS  = [
    "docs/dashboard/nigeria_states.geojson",
    "dashboard/nigeria_states.geojson",
]
PRECISION  = 3   # decimal places — ~110 m resolution, reduces file ~80%


def round_coords(obj):
    """Recursively round all coordinate floats to PRECISION decimal places."""
    if isinstance(obj, list):
        return [round_coords(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, PRECISION)
    return obj


def simplify_feature(f):
    """Keep only NAME_1 property and round all coordinates."""
    return {
        "type": "Feature",
        "properties": {
            "NAME_1": f["properties"].get("NAME_1", ""),
        },
        "geometry": {
            "type":        f["geometry"]["type"],
            "coordinates": round_coords(f["geometry"]["coordinates"]),
        },
    }


def main():
    print("Downloading Nigeria state boundaries from GADM 4.1...")
    print(f"  Source: {GADM_URL}")

    resp = requests.get(GADM_URL, timeout=120, stream=True)
    resp.raise_for_status()

    raw = b""
    for chunk in resp.iter_content(chunk_size=65536):
        raw += chunk
        print(f"  Downloaded {len(raw)/1024:.0f} KB...", end="\r")
    print()

    print("Parsing GeoJSON...")
    gj = json.loads(raw)

    print(f"  {len(gj['features'])} features found")

    simplified = {
        "type":     "FeatureCollection",
        "features": [simplify_feature(f) for f in gj["features"]],
    }

    out_str = json.dumps(simplified, separators=(",", ":"))
    size_kb = len(out_str) / 1024

    for path in OUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(out_str)
        print(f"  Saved → {path}  ({size_kb:.0f} KB)")

    print(f"\nDone. Run the dashboard and states will render as choropleth polygons.")
    print("States in this file:")
    for f in simplified["features"]:
        print(f"  {f['properties']['NAME_1']}")


if __name__ == "__main__":
    main()
