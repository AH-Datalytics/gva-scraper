"""
Daily GVA updater — scrapes the last 30 days to catch late-added incidents.
Commits updated CSVs + a daily summary to a private GitHub repo.

Usage:
    python 06_daily_update.py              # Scrape last 30 days
    python 06_daily_update.py --days 7     # Last 7 days only
"""
import os, csv, sys, time, json, argparse
from datetime import date, timedelta, datetime
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
from importlib.util import spec_from_file_location, module_from_spec
spec = spec_from_file_location("daily", os.path.join(os.path.dirname(__file__), "02_scrape_daily.py"))
daily = module_from_spec(spec)
spec.loader.exec_module(daily)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
FIELDS = daily.FIELDS
DAY_DELAY = 30  # seconds between days (lighter than full scrape)

def load_year_data(year):
    """Load existing CSV for a year. Returns (rows, seen_ids)."""
    csv_path = os.path.join(DATA_DIR, f"gva_{year}.csv")
    rows = []
    seen_ids = set()
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            seen_ids = set(r["incident_id"] for r in rows)
    return rows, seen_ids

def save_year_data(year, rows):
    """Save rows to year CSV."""
    csv_path = os.path.join(DATA_DIR, f"gva_{year}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

def generate_summary():
    """Generate a daily summary CSV with incident/victim counts per day."""
    summary_path = os.path.join(os.path.dirname(__file__), "data", "gva_daily_summary.csv")
    all_rows = []
    for f in sorted(os.listdir(DATA_DIR)):
        if f.startswith("gva_") and f.endswith(".csv"):
            with open(os.path.join(DATA_DIR, f), "r", encoding="utf-8") as fh:
                all_rows.extend(csv.DictReader(fh))

    # Aggregate by date
    from collections import Counter, defaultdict
    stats = defaultdict(lambda: {"incidents": 0, "victims_killed": 0, "victims_injured": 0,
                                  "suspects_killed": 0, "suspects_injured": 0})
    for r in all_rows:
        d = r["date"]
        stats[d]["incidents"] += 1
        stats[d]["victims_killed"] += int(r["victims_killed"] or 0)
        stats[d]["victims_injured"] += int(r["victims_injured"] or 0)
        stats[d]["suspects_killed"] += int(r["suspects_killed"] or 0)
        stats[d]["suspects_injured"] += int(r["suspects_injured"] or 0)

    # Parse and sort dates
    def parse_date(d):
        try:
            return datetime.strptime(d, "%B %d, %Y").date()
        except:
            return None

    sorted_dates = sorted(stats.keys(), key=lambda d: parse_date(d) or date.min)

    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "date_iso", "incidents", "victims_killed", "victims_injured",
                         "suspects_killed", "suspects_injured", "total_shot"])
        for d in sorted_dates:
            s = stats[d]
            iso = parse_date(d)
            total = s["victims_killed"] + s["victims_injured"] + s["suspects_killed"] + s["suspects_injured"]
            writer.writerow([d, iso.isoformat() if iso else "", s["incidents"],
                           s["victims_killed"], s["victims_injured"],
                           s["suspects_killed"], s["suspects_injured"], total])

    print(f"  Summary: {len(sorted_dates)} days written to {summary_path}")
    return summary_path

def git_push(message):
    """Commit and push changes to GitHub."""
    repo_dir = os.path.dirname(__file__)
    os.system(f'cd "{repo_dir}" && git add data/ && git commit -m "{message}" && git push')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Days to look back")
    parser.add_argument("--no-push", action="store_true", help="Skip git push")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)
    today = date.today()
    start = today - timedelta(days=args.days)
    print(f"GVA Daily Update — {start} to {today - timedelta(days=1)} ({args.days} days)")

    # Group dates by year
    dates_by_year = {}
    d = start
    while d < today:
        dates_by_year.setdefault(d.year, []).append(d)
        d += timedelta(days=1)

    total_new = 0

    with sync_playwright() as pw:
        browser, page = daily.launch_browser(pw)

        for year in sorted(dates_by_year.keys()):
            rows, seen_ids = load_year_data(year)
            year_new = 0
            dates = dates_by_year[year]
            print(f"\n  {year}: checking {len(dates)} days ({len(rows)} existing incidents)")

            for i, target in enumerate(dates):
                if i > 0:
                    time.sleep(DAY_DELAY)

                t0 = time.time()
                print(f"    {target} ...", end=" ", flush=True)
                try:
                    result = daily.scrape_day(page, target)
                    if result is not None:
                        new = [r for r in result if r["incident_id"] not in seen_ids]
                        for r in new:
                            seen_ids.add(r["incident_id"])
                        rows.extend(new)
                        year_new += len(new)
                        elapsed = time.time() - t0
                        print(f"{len(new)} new ({elapsed:.0f}s)")
                    else:
                        print(f"FAILED ({time.time()-t0:.0f}s)")
                except Exception as e:
                    print(f"ERROR: {e}")
                    try: browser.close()
                    except: pass
                    time.sleep(60)
                    browser, page = daily.launch_browser(pw)

            if year_new > 0:
                save_year_data(year, rows)
                print(f"  {year}: +{year_new} new incidents (now {len(rows)} total)")
            else:
                print(f"  {year}: no new incidents")

            total_new += year_new

        browser.close()

    print(f"\nTotal new incidents: {total_new}")

    # Generate summary
    generate_summary()

    # Git push
    if not args.no_push and total_new > 0:
        git_push(f"Daily update: +{total_new} incidents ({today.isoformat()})")

    print("Done!")

if __name__ == "__main__":
    main()
