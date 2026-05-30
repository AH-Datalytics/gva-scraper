"""
Repair a scraped year by re-scraping days with suspicious page-multiple counts (25/50/75/100).
These are days where pagination likely stopped early.
Also re-scrapes any missing days.

Usage:
    python 05_repair.py 2024
    python 05_repair.py 2023 2024 2025 2026
"""
import os, csv, sys, time
from datetime import date, timedelta, datetime
from collections import Counter
from playwright.sync_api import sync_playwright

# Import scrape functions from daily scraper
sys.path.insert(0, os.path.dirname(__file__))
from importlib.util import spec_from_file_location, module_from_spec
spec = spec_from_file_location("daily", os.path.join(os.path.dirname(__file__), "02_scrape_daily.py"))
daily = module_from_spec(spec)
spec.loader.exec_module(daily)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
FIELDS = daily.FIELDS
REPAIR_DELAY = 60  # seconds between days

def find_problem_days(year):
    """Find missing days and days with suspicious page-multiple counts."""
    csv_path = os.path.join(DATA_DIR, f"gva_{year}.csv")
    if not os.path.exists(csv_path):
        print(f"  No data file for {year}")
        return [], []

    with open(csv_path, "r", encoding="utf-8") as f:
        by_date = Counter(r["date"] for r in csv.DictReader(f))

    # Parse scraped dates
    scraped_dates = {}
    for dt_str, count in by_date.items():
        try:
            d = datetime.strptime(dt_str, "%B %d, %Y").date()
            scraped_dates[d] = count
        except:
            pass

    # All dates in the year
    start = date(year, 1, 1)
    end = min(date(year, 12, 31), date.today())
    all_dates = set()
    d = start
    while d <= end:
        all_dates.add(d)
        d += timedelta(days=1)

    missing = sorted(all_dates - set(scraped_dates.keys()))
    suspicious = sorted(d for d, c in scraped_dates.items() if c % 25 == 0 and c <= 100)

    return missing, suspicious

def repair_year(pw, year):
    """Re-scrape problem days for a year."""
    csv_path = os.path.join(DATA_DIR, f"gva_{year}.csv")
    missing, suspicious = find_problem_days(year)

    print(f"  {year}: {len(missing)} missing, {len(suspicious)} suspicious (page-multiple)")
    if not missing and not suspicious:
        print(f"  Nothing to repair!")
        return

    repair_days = sorted(set(missing + suspicious))
    print(f"  Re-scraping {len(repair_days)} days...\n")

    # Load existing data
    with open(csv_path, "r", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    # Remove rows for suspicious days (will be replaced with fresh data)
    suspicious_strs = set()
    for d in suspicious:
        # GVA uses "Month D, YYYY" format (no leading zero on day)
        suspicious_strs.add(d.strftime("%B ") + str(d.day) + d.strftime(", %Y"))
    all_rows = [r for r in all_rows if r["date"] not in suspicious_strs]
    seen_ids = set(r["incident_id"] for r in all_rows)

    before_count = len(all_rows)
    browser, page = daily.launch_browser(pw)
    consecutive_errors = 0

    for i, d in enumerate(repair_days):
        if i > 0:
            time.sleep(REPAIR_DELAY)

        is_missing = d in missing
        label = "MISSING" if is_missing else "TRUNCATED"
        t0 = time.time()
        print(f"    [{label}] {d} ...", end=" ", flush=True)

        try:
            result = daily.scrape_day(page, d)
            if result is not None:
                new = [r for r in result if r["incident_id"] not in seen_ids]
                for r in new:
                    seen_ids.add(r["incident_id"])
                all_rows.extend(new)
                elapsed = time.time() - t0
                print(f"{len(new)} new incidents ({elapsed:.0f}s)")
                consecutive_errors = 0
            else:
                elapsed = time.time() - t0
                consecutive_errors += 1
                print(f"FAILED ({elapsed:.0f}s)")
                if consecutive_errors >= 3:
                    print("    Relaunching browser + 90s cooldown...")
                    try: browser.close()
                    except: pass
                    time.sleep(90)
                    browser, page = daily.launch_browser(pw)
                    consecutive_errors = 0
        except Exception as e:
            elapsed = time.time() - t0
            consecutive_errors += 1
            print(f"ERROR ({elapsed:.0f}s): {e}")
            if consecutive_errors >= 3:
                print("    Relaunching browser + 90s cooldown...")
                try: browser.close()
                except: pass
                time.sleep(90)
                browser, page = daily.launch_browser(pw)
                consecutive_errors = 0

        # Save every 5 days
        if (i + 1) % 5 == 0 or i == len(repair_days) - 1:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(all_rows)

    try: browser.close()
    except: pass

    after_count = len(all_rows)
    print(f"\n  {year} repair done: {before_count} -> {after_count} incidents (+{after_count - before_count})")

def main():
    if len(sys.argv) < 2:
        print("Usage: python 05_repair.py YEAR [YEAR ...]")
        return

    years = [int(y) for y in sys.argv[1:]]
    sys.stdout.reconfigure(line_buffering=True)

    print(f"GVA Repair — years: {years}")
    print(f"Delay: {REPAIR_DELAY}s between days\n")

    for year in years:
        print(f"\n=== {year} ===")
        # First just show what needs repair
        missing, suspicious = find_problem_days(year)
        if suspicious:
            csv_path = os.path.join(DATA_DIR, f"gva_{year}.csv")
            with open(csv_path, "r", encoding="utf-8") as f:
                by_date = Counter(r["date"] for r in csv.DictReader(f))
            for d in suspicious:
                dt_str = d.strftime("%B ") + str(d.day) + d.strftime(", %Y")
                print(f"    {d}: {by_date.get(dt_str, '?')} incidents (page multiple)")

    with sync_playwright() as pw:
        for year in years:
            print(f"\n=== Repairing {year} ===")
            try:
                repair_year(pw, year)
            except Exception as e:
                print(f"  Year {year} repair failed: {e}")

    print("\nRepair complete!")

if __name__ == "__main__":
    main()
