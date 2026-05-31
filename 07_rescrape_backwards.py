"""
Re-scrape backwards from a given month until we find 0 new incidents for a full month.
This identifies how far back GVA's incomplete data goes.

Usage:
    python 07_rescrape_backwards.py              # Start from current month
    python 07_rescrape_backwards.py 2026-03       # Start from March 2026
"""
import os, csv, sys, time, argparse
from datetime import date, timedelta
from collections import Counter
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
from importlib.util import spec_from_file_location, module_from_spec
spec = spec_from_file_location("daily", os.path.join(os.path.dirname(__file__), "02_scrape_daily.py"))
daily = module_from_spec(spec)
spec.loader.exec_module(daily)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
FIELDS = daily.FIELDS
DAY_DELAY = 30

def get_month_days(year, month):
    """Get all days in a month."""
    d = date(year, month, 1)
    days = []
    while d.month == month and d <= date.today():
        days.append(d)
        d += timedelta(days=1)
    return days

def prev_month(year, month):
    if month == 1:
        return year - 1, 12
    return year, month - 1

def rescrape_month(pw, browser, page, year, month):
    """Re-scrape a month, return (new_count, total_scraped, browser, page)."""
    csv_path = os.path.join(DATA_DIR, f"gva_{year}.csv")

    # Load existing
    rows = []
    seen_ids = set()
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            seen_ids = set(r["incident_id"] for r in rows)

    days = get_month_days(year, month)
    month_name = date(year, month, 1).strftime("%B %Y")
    total_new = 0
    consecutive_errors = 0

    print(f"\n  {month_name}: {len(days)} days, {len(rows)} existing incidents")

    for i, d in enumerate(days):
        if i > 0:
            time.sleep(DAY_DELAY)

        t0 = time.time()
        print(f"    {d} ...", end=" ", flush=True)
        try:
            result = daily.scrape_day(page, d)
            if result is not None:
                new = [r for r in result if r["incident_id"] not in seen_ids]
                for r in new:
                    seen_ids.add(r["incident_id"])
                rows.extend(new)
                total_new += len(new)
                elapsed = time.time() - t0
                marker = f" +{len(new)}" if len(new) > 0 else ""
                print(f"{len(new)} new ({elapsed:.0f}s){marker}")
                consecutive_errors = 0
            else:
                print(f"FAILED ({time.time()-t0:.0f}s)")
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    print("    Relaunching browser + 90s cooldown...")
                    try: browser.close()
                    except: pass
                    time.sleep(90)
                    browser, page = daily.launch_browser(pw)
                    consecutive_errors = 0
        except Exception as e:
            print(f"ERROR ({time.time()-t0:.0f}s): {e}")
            consecutive_errors += 1
            if consecutive_errors >= 3:
                try: browser.close()
                except: pass
                time.sleep(90)
                browser, page = daily.launch_browser(pw)
                consecutive_errors = 0

    # Save
    if total_new > 0:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    print(f"  {month_name}: +{total_new} new incidents")
    return total_new, browser, page

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start", nargs="?", default=None, help="Start month YYYY-MM")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    if args.start:
        year, month = map(int, args.start.split("-"))
    else:
        today = date.today()
        year, month = today.year, today.month

    print(f"Re-scraping backwards from {year}-{month:02d}")
    print(f"Will stop when a full month has 0 new incidents\n")

    with sync_playwright() as pw:
        browser, page = daily.launch_browser(pw)

        zero_months = 0
        while year >= 2017:
            new_count, browser, page = rescrape_month(pw, browser, page, year, month)

            if new_count == 0:
                zero_months += 1
                if zero_months >= 2:
                    print(f"\n  Two consecutive months with 0 new — stopping.")
                    break
            else:
                zero_months = 0

            year, month = prev_month(year, month)

        browser.close()

    print("\nDone!")

if __name__ == "__main__":
    main()
