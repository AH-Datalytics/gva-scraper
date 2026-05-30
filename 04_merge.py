"""
Merge the jamesqo base dataset (2013-2018) with scraped GVA data (2018+)
into a single unified CSV. Deduplicates by incident_id.

The base dataset has: n_killed, n_injured (total)
The scraped data has: victims_killed, victims_injured, suspects_killed, suspects_injured, suspects_arrested

Both are preserved. For base data, n_killed/n_injured map to victims_killed/victims_injured
(the base dataset only tracked victim counts).
"""
import os, csv, glob
from datetime import datetime

BASE_DIR = os.path.join(os.path.dirname(__file__), "data", "base")
RAW_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
OUTPUT = os.path.join(os.path.dirname(__file__), "data", "gva_all_shootings.csv")

FIELDS = [
    "incident_id", "date", "state", "city_or_county", "address",
    "victims_killed", "victims_injured", "suspects_killed",
    "suspects_injured", "suspects_arrested",
    "latitude", "longitude",
    "incident_characteristics", "guns_involved", "n_guns_involved",
    "notes", "congressional_district",
    "source",
]

def parse_date(d):
    """Normalize date strings to YYYY-MM-DD for sorting."""
    if not d:
        return ""
    # jamesqo format: "2018-03-31" or similar
    if "-" in d and len(d) == 10:
        return d
    # GVA reports format: "January 15, 2024"
    try:
        return datetime.strptime(d, "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        pass
    # Try other formats
    for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y"]:
        try:
            return datetime.strptime(d.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return d

def load_base():
    """Load the filtered jamesqo base dataset."""
    path = os.path.join(BASE_DIR, "gva_2013_2018_shootings.csv")
    if not os.path.exists(path):
        print("Base dataset not found. Run 01_download_base.py first.")
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "incident_id": row.get("incident_id", ""),
                "date": parse_date(row.get("date", "")),
                "state": row.get("state", ""),
                "city_or_county": row.get("city_or_county", ""),
                "address": row.get("address", ""),
                "victims_killed": row.get("n_killed", "0"),
                "victims_injured": row.get("n_injured", "0"),
                "suspects_killed": "",
                "suspects_injured": "",
                "suspects_arrested": "",
                "latitude": row.get("latitude", ""),
                "longitude": row.get("longitude", ""),
                "incident_characteristics": row.get("incident_characteristics", ""),
                "guns_involved": row.get("gun_type", ""),
                "n_guns_involved": row.get("n_guns_involved", ""),
                "notes": row.get("notes", ""),
                "congressional_district": row.get("congressional_district", ""),
                "source": "base",
            })
    return rows

def load_scraped():
    """Load scraped yearly CSVs."""
    rows = []
    raw_files = sorted(glob.glob(os.path.join(RAW_DIR, "gva_*.csv")))
    for raw_path in raw_files:
        with open(raw_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({
                    "incident_id": row.get("incident_id", ""),
                    "date": parse_date(row.get("date", "")),
                    "state": row.get("state", ""),
                    "city_or_county": row.get("city_or_county", ""),
                    "address": row.get("address", ""),
                    "victims_killed": row.get("victims_killed", "0"),
                    "victims_injured": row.get("victims_injured", "0"),
                    "suspects_killed": row.get("suspects_killed", "0"),
                    "suspects_injured": row.get("suspects_injured", "0"),
                    "suspects_arrested": row.get("suspects_arrested", "0"),
                    "latitude": "",
                    "longitude": "",
                    "incident_characteristics": "",
                    "guns_involved": "",
                    "n_guns_involved": "",
                    "notes": "",
                    "congressional_district": "",
                    "source": "scraped",
                })
    return rows

def main():
    base = load_base()
    print(f"Base dataset: {len(base):,} incidents")

    scraped = load_scraped()
    print(f"Scraped data: {len(scraped):,} incidents")

    # Deduplicate by incident_id — scraped wins for overlap period (has suspect data)
    seen = {}
    for row in scraped:
        if row["incident_id"]:
            seen[row["incident_id"]] = row
    for row in base:
        if row["incident_id"] and row["incident_id"] not in seen:
            seen[row["incident_id"]] = row

    # Sort by date
    all_rows = sorted(seen.values(), key=lambda r: r.get("date", ""))

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nMerged: {len(all_rows):,} unique incidents")
    print(f"Saved to {OUTPUT}")

    if all_rows:
        print(f"Range: {all_rows[0]['date']} to {all_rows[-1]['date']}")

        # Year breakdown
        by_year = {}
        for r in all_rows:
            y = r["date"][:4] if r["date"] else "unknown"
            by_year[y] = by_year.get(y, 0) + 1
        print("\nBy year:")
        for y in sorted(by_year):
            print(f"  {y}: {by_year[y]:,}")

if __name__ == "__main__":
    main()
