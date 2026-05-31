"""Quick re-scrape of a single day. Usage: python rescrape_day.py 2026-05-03"""
import sys, os, csv, time
from datetime import date
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
from importlib.util import spec_from_file_location, module_from_spec
spec = spec_from_file_location("daily", os.path.join(os.path.dirname(__file__), "02_scrape_daily.py"))
daily = module_from_spec(spec)
spec.loader.exec_module(daily)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
FIELDS = daily.FIELDS

target = date.fromisoformat(sys.argv[1])
year = target.year
csv_path = os.path.join(DATA_DIR, f"gva_{year}.csv")

# Load existing
with open(csv_path, "r", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
seen_ids = set(r["incident_id"] for r in rows)
before = len(rows)

with sync_playwright() as pw:
    browser, page = daily.launch_browser(pw)
    result = daily.scrape_day(page, target)
    browser.close()

if result:
    new = [r for r in result if r["incident_id"] not in seen_ids]
    rows.extend(new)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{target}: {len(result)} scraped, {len(new)} new (was {before}, now {len(rows)})")
else:
    print(f"{target}: FAILED")
