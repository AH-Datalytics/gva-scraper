"""
Enrich scraped GVA incidents with detail page data (lat/lon, participants, guns, etc.).
Uses plain requests (no Playwright needed) since incident pages are static HTML.
Respects 10s crawl delay. Saves progress incrementally.

Usage:
    python 03_enrich_incidents.py                     # Enrich all raw files
    python 03_enrich_incidents.py --file gva_2023_01  # Specific month
"""
import os, csv, time, json, argparse, re, glob
from bs4 import BeautifulSoup
import requests

RAW_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
ENRICHED_DIR = os.path.join(os.path.dirname(__file__), "data", "enriched")
PROGRESS_FILE = os.path.join(ENRICHED_DIR, "enrich_progress.json")
CRAWL_DELAY = 10
INCIDENT_URL = "https://www.gunviolencearchive.org/incident/{}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

FIELDS = [
    "incident_id", "date", "state", "city_or_county", "address",
    "n_killed", "n_injured", "latitude", "longitude",
    "participants", "incident_characteristics", "guns_involved",
    "notes", "congressional_district",
]

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {}

def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)

def parse_incident_page(html):
    """Extract detail fields from an incident page."""
    soup = BeautifulSoup(html, "lxml")
    data = {
        "latitude": "", "longitude": "",
        "participants": "", "incident_characteristics": "",
        "guns_involved": "", "notes": "", "congressional_district": "",
    }

    # Geolocation — often in a script tag or span
    geo_span = soup.find("span", class_="field-name-field-geolocation")
    if geo_span:
        text = geo_span.get_text(separator=" ")
        coords = re.findall(r'[-+]?\d+\.\d+', text)
        if len(coords) >= 2:
            data["latitude"] = coords[0]
            data["longitude"] = coords[1]

    # Also check for lat/lon in hidden inputs or script
    for script in soup.find_all("script"):
        if script.string and "latitude" in (script.string or ""):
            lat_match = re.search(r'"latitude"\s*:\s*"?([-\d.]+)', script.string)
            lon_match = re.search(r'"longitude"\s*:\s*"?([-\d.]+)', script.string)
            if lat_match:
                data["latitude"] = lat_match.group(1)
            if lon_match:
                data["longitude"] = lon_match.group(1)

    # Sections by h2 headers
    main = soup.find("div", id="block-system-main")
    if not main:
        return data

    sections = {}
    current_header = None
    for el in main.descendants:
        if el.name == "h2":
            current_header = el.get_text(strip=True).lower()
            sections[current_header] = []
        elif current_header and el.name in ("li", "p", "div") and el.string:
            sections[current_header].append(el.get_text(strip=True))

    # Map sections to fields
    for key in sections:
        text = " | ".join(sections[key])
        if "participant" in key:
            data["participants"] = text
        elif "characteristic" in key:
            data["incident_characteristics"] = text
        elif "gun" in key:
            data["guns_involved"] = text
        elif "note" in key:
            data["notes"] = text
        elif "district" in key:
            data["congressional_district"] = text

    return data

def enrich_file(raw_path, progress):
    """Enrich a single raw CSV with incident detail data."""
    basename = os.path.splitext(os.path.basename(raw_path))[0]
    out_path = os.path.join(ENRICHED_DIR, f"{basename}_enriched.csv")

    # Load raw incidents
    with open(raw_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        raw_incidents = list(reader)

    if not raw_incidents:
        return

    # Load existing enriched data
    enriched_ids = set(progress.get(basename, []))
    existing = []
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    print(f"\n{basename}: {len(raw_incidents)} incidents, {len(enriched_ids)} already enriched")

    session = requests.Session()
    session.headers.update(HEADERS)

    for i, row in enumerate(raw_incidents):
        iid = row["incident_id"]
        if not iid or iid in enriched_ids:
            continue

        time.sleep(CRAWL_DELAY)
        url = INCIDENT_URL.format(iid)
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 404:
                print(f"  {iid}: 404, skipping")
                enriched_ids.add(iid)
                continue
            resp.raise_for_status()
            detail = parse_incident_page(resp.text)
        except Exception as e:
            print(f"  {iid}: error {e}, skipping")
            continue

        enriched_row = {**row, **detail}
        existing.append(enriched_row)
        enriched_ids.add(iid)

        # Save incrementally every 10 incidents
        if len(enriched_ids) % 10 == 0:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                for r in existing:
                    writer.writerow({k: r.get(k, "") for k in FIELDS})
            progress[basename] = list(enriched_ids)
            save_progress(progress)

        remaining = len(raw_incidents) - len(enriched_ids)
        print(f"  {iid}: +{row['n_killed']}k/{row['n_injured']}i | {detail.get('latitude','?')},{detail.get('longitude','?')} | {remaining} remaining")

    # Final save
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for r in existing:
            writer.writerow({k: r.get(k, "") for k in FIELDS})
    progress[basename] = list(enriched_ids)
    save_progress(progress)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=None, help="Specific file basename (e.g., gva_2023_01)")
    args = parser.parse_args()

    progress = load_progress()

    if args.file:
        raw_path = os.path.join(RAW_DIR, f"{args.file}.csv")
        if not os.path.exists(raw_path):
            print(f"File not found: {raw_path}")
            return
        enrich_file(raw_path, progress)
    else:
        raw_files = sorted(glob.glob(os.path.join(RAW_DIR, "gva_*.csv")))
        print(f"Found {len(raw_files)} raw files to enrich")
        for raw_path in raw_files:
            enrich_file(raw_path, progress)

    print("\nEnrichment complete!")

if __name__ == "__main__":
    main()
