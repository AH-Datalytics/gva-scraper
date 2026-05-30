"""
Scrape Gun Violence Archive using daily queries + HTML table extraction.
No batch/export endpoint (avoids aggressive rate limiting).

Paginates until duplicate IDs detected (no reliance on pager links).
Retries failed days at the end of each year.
60s delay between days to avoid IP blocks.

Usage:
    python 02_scrape_daily.py --start 2024 --end 2024
    python 02_scrape_daily.py --start 2018 --end 2026
"""
import os, csv, time, json, argparse, sys
from datetime import date, timedelta, datetime
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
GVA_QUERY_URL = "https://www.gunviolencearchive.org/query"
DAY_DELAY = 60  # seconds between days

FIELDS = [
    "incident_id", "date", "state", "city_or_county", "address",
    "victims_killed", "victims_injured", "suspects_killed",
    "suspects_injured", "suspects_arrested",
]

def progress_file(year):
    return os.path.join(DATA_DIR, f"progress_{year}.json")

def load_progress(year):
    pf = progress_file(year)
    if os.path.exists(pf):
        with open(pf) as f:
            return json.load(f)
    return {}

def save_progress(progress, year):
    with open(progress_file(year), "w") as f:
        json.dump(progress, f, indent=2)

def submit_query(page, query_date, retries=2):
    """Submit query form for a single day. Returns query path or None."""
    d = query_date
    date_str = f"{d.month}/{d.day}/{d.year}"

    for attempt in range(retries + 1):
        try:
            page.goto(GVA_QUERY_URL, timeout=60000)
            time.sleep(2)

            page.evaluate("""() => {
                const s = document.querySelector('#edit-query-filters-new-type');
                s.value = 'IncidentDate';
                s.dispatchEvent(new Event('change', { bubbles: true }));
            }""")
            time.sleep(3)

            has_date = page.evaluate("!!document.querySelector('input[name*=\"date-from\"]')")
            if not has_date:
                if attempt < retries:
                    time.sleep(3)
                    continue
                return None

            page.evaluate(f"""() => {{
                const f = document.querySelector('input[name*="date-from"]');
                const t = document.querySelector('input[name*="date-to"]');
                f.value = '{date_str}';
                f.dispatchEvent(new Event('change', {{ bubbles: true }}));
                t.value = '{date_str}';
                t.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}""")
            time.sleep(1)

            page.evaluate("document.querySelector('input[value=\"Search\"]').click()")
            time.sleep(4)

            try:
                page.wait_for_selector("table tr td", timeout=15000)
            except PwTimeout:
                if attempt < retries:
                    time.sleep(3)
                    continue
                return None

            # Get query path from any pager link
            query_path = page.evaluate("""() => {
                const links = document.querySelectorAll('.pager a[href*="/query/"]');
                for (const a of links) {
                    const href = a.getAttribute('href');
                    if (href) return href.split('?')[0];
                }
                return null;
            }""")
            return query_path or "SINGLE_PAGE"

        except Exception as e:
            if attempt < retries:
                time.sleep(5)
                continue
            return None

    return None

def extract_rows(page):
    """Extract incident rows from the current page via JS."""
    return page.evaluate("""() => {
        const rows = document.querySelectorAll('table tr');
        const result = [];
        for (const row of rows) {
            const cells = row.querySelectorAll('td');
            if (cells.length < 10) continue;
            const link = row.querySelector('a[href*="/incident/"]');
            const incidentId = link ? link.getAttribute('href').match(/\\/(\\d+)/)?.[1] || '' : '';
            result.push({
                incident_id: incidentId,
                date: cells[1].textContent.trim(),
                state: cells[2].textContent.trim(),
                city_or_county: cells[3].textContent.trim(),
                address: cells[4].textContent.trim(),
                victims_killed: cells[5].textContent.trim(),
                victims_injured: cells[6].textContent.trim(),
                suspects_killed: cells[7].textContent.trim(),
                suspects_injured: cells[8].textContent.trim(),
                suspects_arrested: cells[9].textContent.trim(),
            });
        }
        return result;
    }""")

def filter_shot(rows):
    """Keep only incidents where someone was actually shot."""
    return [r for r in rows if (
        int(r["victims_killed"] or 0) + int(r["victims_injured"] or 0) +
        int(r["suspects_killed"] or 0) + int(r["suspects_injured"] or 0) > 0
    )]

def scrape_day(page, query_date):
    """Scrape all incidents for a single day via table pagination."""
    all_rows = []
    query_path = submit_query(page, query_date)
    if query_path is None:
        return None  # distinguish failure from empty

    # First page already loaded
    rows = extract_rows(page)
    all_rows.extend(filter_shot(rows))

    if query_path == "SINGLE_PAGE":
        return all_rows

    # Paginate until duplicates or empty (max ~20 pages per day)
    seen_ids = set(r["incident_id"] for r in all_rows)
    pg = 1
    while pg < 20:
        time.sleep(2)
        url = f"https://www.gunviolencearchive.org{query_path}?page={pg}"
        page.goto(url, timeout=60000)
        time.sleep(3)

        try:
            page.wait_for_selector("table tr td", timeout=10000)
        except PwTimeout:
            break

        rows = extract_rows(page)
        if not rows:
            break

        new_rows = [r for r in rows if r["incident_id"] not in seen_ids]
        if not new_rows:
            break  # all duplicates = past the last page

        for r in new_rows:
            seen_ids.add(r["incident_id"])
        all_rows.extend(filter_shot(new_rows))

        if len(rows) < 25:
            break  # partial page = last page
        pg += 1

    return all_rows

def launch_browser(pw):
    browser = pw.chromium.launch(headless=False)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    return browser, context.new_page()

def save_csv(all_incidents, output_path):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(all_incidents)

def scrape_year(pw, year):
    """Scrape all days for a year with retry passes for failures."""
    progress = load_progress(year)
    year_key = str(year)
    output_path = os.path.join(DATA_DIR, f"gva_{year}.csv")

    if progress.get("status") == "done":
        print(f"  Skipping {year} (already completed)")
        return

    start = date(year, 1, 1)
    end = min(date(year, 12, 31), date.today())
    total_days = (end - start).days + 1

    # Load existing data
    completed_days = set(progress.get("completed_days", []))
    failed_days = set(progress.get("failed_days", []))
    all_incidents = []
    seen_ids = set()
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            all_incidents = list(csv.DictReader(f))
            seen_ids = set(r["incident_id"] for r in all_incidents)

    browser, page = launch_browser(pw)
    consecutive_errors = 0

    # First pass: scrape all uncompleted days
    remaining = [start + timedelta(days=i) for i in range(total_days)
                 if (start + timedelta(days=i)).isoformat() not in completed_days]
    print(f"  {year}: {total_days} days, {len(completed_days)} already done, {len(remaining)} remaining")

    for idx, current_date in enumerate(remaining):
        day_str = current_date.isoformat()

        if idx > 0:
            time.sleep(DAY_DELAY)

        t0 = time.time()
        print(f"    {current_date} ({len(completed_days)+1}/{total_days}) ...", end=" ", flush=True)

        try:
            result = scrape_day(page, current_date)
            if result is not None:
                new = [r for r in result if r["incident_id"] not in seen_ids]
                for r in new:
                    seen_ids.add(r["incident_id"])
                all_incidents.extend(new)
                elapsed = time.time() - t0
                print(f"{len(new)} incidents ({elapsed:.0f}s) [{len(all_incidents)} total]")
                completed_days.add(day_str)
                if day_str in failed_days:
                    failed_days.discard(day_str)
                consecutive_errors = 0
            else:
                elapsed = time.time() - t0
                consecutive_errors += 1
                failed_days.add(day_str)
                print(f"FAILED ({elapsed:.0f}s)")
                if consecutive_errors >= 3:
                    print("    Relaunching browser + 90s cooldown...")
                    try: browser.close()
                    except: pass
                    time.sleep(90)
                    browser, page = launch_browser(pw)
                    consecutive_errors = 0
        except Exception as e:
            elapsed = time.time() - t0
            consecutive_errors += 1
            failed_days.add(day_str)
            print(f"ERROR ({elapsed:.0f}s): {e}")
            if consecutive_errors >= 3:
                print("    Relaunching browser + 90s cooldown...")
                try: browser.close()
                except: pass
                time.sleep(90)
                browser, page = launch_browser(pw)
                consecutive_errors = 0

        # Save every 7 days
        if (len(completed_days) % 7 == 0) or idx == len(remaining) - 1:
            save_csv(all_incidents, output_path)
            progress[year_key] = len(completed_days)
            progress["completed_days"] = sorted(completed_days)
            progress["failed_days"] = sorted(failed_days)
            save_progress(progress, year)

    # Retry failed days (up to 3 passes)
    for retry_pass in range(3):
        retry_list = sorted(failed_days)
        if not retry_list:
            break
        print(f"\n  Retry pass {retry_pass + 1}: {len(retry_list)} failed days")
        print(f"  Waiting 180s for rate limit to clear...")
        time.sleep(180)
        try: browser.close()
        except: pass
        browser, page = launch_browser(pw)

        for day_str in retry_list:
            time.sleep(DAY_DELAY)
            current_date = date.fromisoformat(day_str)
            t0 = time.time()
            print(f"    [RETRY] {current_date} ...", end=" ", flush=True)
            try:
                result = scrape_day(page, current_date)
                if result is not None:
                    new = [r for r in result if r["incident_id"] not in seen_ids]
                    for r in new:
                        seen_ids.add(r["incident_id"])
                    all_incidents.extend(new)
                    elapsed = time.time() - t0
                    print(f"{len(new)} incidents ({elapsed:.0f}s)")
                    completed_days.add(day_str)
                    failed_days.discard(day_str)
                else:
                    print(f"FAILED ({time.time()-t0:.0f}s)")
            except Exception as e:
                print(f"ERROR ({time.time()-t0:.0f}s): {e}")

        save_csv(all_incidents, output_path)
        progress["completed_days"] = sorted(completed_days)
        progress["failed_days"] = sorted(failed_days)
        save_progress(progress, year)

    # Mark done
    progress["status"] = "done"
    save_progress(progress, year)
    try: browser.close()
    except: pass

    if failed_days:
        print(f"  {year} done with {len(failed_days)} unrecoverable days: {sorted(failed_days)}")
    print(f"  {year} complete: {len(all_incidents)} shooting incidents across {len(completed_days)} days")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=2018)
    parser.add_argument("--end", type=int, default=None)
    args = parser.parse_args()

    end_year = args.end or date.today().year
    sys.stdout.reconfigure(line_buffering=True)

    print(f"GVA Daily Scraper — {args.start} to {end_year}")
    print(f"Strategy: daily queries, table pagination, {DAY_DELAY}s delay")
    print(f"Filter: victims/suspects killed+injured > 0\n")

    with sync_playwright() as pw:
        for year in range(args.start, end_year + 1):
            print(f"\n=== {year} ===")
            try:
                scrape_year(pw, year)
            except Exception as e:
                print(f"  Year {year} failed: {e}")

    print("\nScraping complete!")

if __name__ == "__main__":
    main()
