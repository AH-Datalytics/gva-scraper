"""
Scrape Gun Violence Archive using the CSV export feature.
Queries in 2-week chunks (under 2,000 result cap), clicks Export as CSV,
downloads the full CSV. No pagination needed.

~26 chunks per year × ~15s each = ~7 minutes per year.

Usage:
    python 02_scrape_gva.py                    # Scrape 2018 – today
    python 02_scrape_gva.py --start 2024       # From 2024
    python 02_scrape_gva.py --start 2023 --end 2024
"""
import os, csv, time, json, argparse, sys, io
from datetime import date, timedelta, datetime
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
HAS_STEALTH = False

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
GVA_QUERY_URL = "https://www.gunviolencearchive.org/query"
CHUNK_DAYS = 14  # 2-week chunks, ~1,500 incidents each

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

def submit_and_export(page, from_date, to_date, retries=2):
    """Submit query, click Export CSV, download the file. Returns CSV text or None."""
    from_str = f"{from_date.month}/{from_date.day}/{from_date.year}"
    to_str = f"{to_date.month}/{to_date.day}/{to_date.year}"

    for attempt in range(retries + 1):
        try:
            # Step 1: Load query page
            page.goto(GVA_QUERY_URL, timeout=60000)
            time.sleep(2)

            # Step 2: Add date filter
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

            # Step 3: Fill dates and submit
            page.evaluate(f"""() => {{
                const f = document.querySelector('input[name*="date-from"]');
                const t = document.querySelector('input[name*="date-to"]');
                f.value = '{from_str}';
                f.dispatchEvent(new Event('change', {{ bubbles: true }}));
                t.value = '{to_str}';
                t.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}""")
            time.sleep(1)
            page.evaluate("document.querySelector('input[value=\"Search\"]').click()")
            time.sleep(5)

            try:
                page.wait_for_selector("table tr td", timeout=15000)
            except PwTimeout:
                if attempt < retries:
                    continue
                return None

            # Step 4: Click Export as CSV (obfuscated button)
            export_btn = page.query_selector("a.button[href='#']")
            if not export_btn:
                if attempt < retries:
                    continue
                return None

            export_btn.click()
            time.sleep(3)

            # Step 5: Wait for export to complete (page content changes, URL may not)
            for _ in range(60):
                done = page.evaluate("""() => {
                    return document.body.innerText.includes('EXPORT COMPLETE')
                        || !!document.querySelector('a[href*="download"]');
                }""")
                if done:
                    break
                time.sleep(2)

            # Step 6: Fetch CSV via JavaScript fetch() in browser context
            dl_href = page.evaluate("""() => {
                const a = document.querySelector('a[href*="download"]');
                return a ? a.getAttribute('href') : null;
            }""")
            if not dl_href:
                if attempt < retries:
                    continue
                return None

            csv_text = page.evaluate("""async (href) => {
                const resp = await fetch(href);
                return await resp.text();
            }""", dl_href)

            if csv_text and len(csv_text) > 50 and "Incident ID" in csv_text:
                return csv_text

            if attempt < retries:
                continue
            return None

        except Exception as e:
            if attempt < retries:
                print(f"[retry {attempt+1}: {e}] ", end="", flush=True)
                time.sleep(5)
                continue
            print(f"[FAILED: {e}] ", end="", flush=True)
            return None

    return None

def parse_csv_export(csv_text):
    """Parse GVA CSV export into standardized row dicts."""
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for r in reader:
        rows.append({
            "incident_id": r.get("Incident ID", ""),
            "date": r.get("Incident Date", ""),
            "state": r.get("State", ""),
            "city_or_county": r.get("City Or County", ""),
            "address": r.get("Address", ""),
            "victims_killed": r.get("Victims Killed", "0"),
            "victims_injured": r.get("Victims Injured", "0"),
            "suspects_killed": r.get("Suspects Killed", "0"),
            "suspects_injured": r.get("Suspects Injured", "0"),
            "suspects_arrested": r.get("Suspects Arrested", "0"),
        })
    return rows

def filter_shot(rows):
    """Keep only incidents where someone was actually shot."""
    return [r for r in rows if (
        int(r["victims_killed"] or 0) + int(r["victims_injured"] or 0) +
        int(r["suspects_killed"] or 0) + int(r["suspects_injured"] or 0) > 0
    )]

def generate_chunks(year):
    """Generate (from_date, to_date) tuples in CHUNK_DAYS increments."""
    start = date(year, 1, 1)
    end = min(date(year, 12, 31), date.today())
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=CHUNK_DAYS - 1), end)
        yield (current, chunk_end)
        current = chunk_end + timedelta(days=1)

def launch_browser(pw):
    browser = pw.chromium.launch(headless=False)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    if HAS_STEALTH:
        try:
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass
    return browser, page

def scrape_year(pw, year):
    """Scrape all chunks for a given year via CSV export."""
    progress = load_progress(year)
    year_key = str(year)
    output_path = os.path.join(DATA_DIR, f"gva_{year}.csv")

    if progress.get(year_key) == "done":
        print(f"  Skipping {year} (already completed)")
        return

    last_chunk = progress.get(year_key, 0)
    if isinstance(last_chunk, str):
        last_chunk = 0

    chunks = list(generate_chunks(year))

    # Load existing data if resuming
    existing = []
    seen_ids = set()
    if os.path.exists(output_path) and last_chunk > 0:
        with open(output_path, "r", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
            seen_ids = set(r["incident_id"] for r in existing)

    all_incidents = existing
    browser, page = launch_browser(pw)
    failed_chunks = []
    consecutive_errors = 0

    print(f"  {year}: {len(chunks)} chunks of {CHUNK_DAYS} days, resuming from chunk {last_chunk}")

    def do_chunk(i, from_d, to_d, is_retry=False):
        nonlocal all_incidents, seen_ids, browser, page, consecutive_errors
        prefix = "[RETRY] " if is_retry else ""
        t0 = time.time()
        print(f"    {prefix}Chunk {i+1}/{len(chunks)}: {from_d} to {to_d} ...", end=" ", flush=True)

        csv_text = submit_and_export(page, from_d, to_d)
        if csv_text:
            rows = parse_csv_export(csv_text)
            shot = filter_shot(rows)
            new = [r for r in shot if r["incident_id"] not in seen_ids]
            for r in new:
                seen_ids.add(r["incident_id"])
            all_incidents.extend(new)
            elapsed = time.time() - t0
            print(f"{len(rows)} total, {len(new)} new shots ({elapsed:.0f}s) [{len(all_incidents)} cumulative]")
            consecutive_errors = 0
            return True
        else:
            elapsed = time.time() - t0
            consecutive_errors += 1
            print(f"FAILED ({elapsed:.0f}s)")
            if consecutive_errors >= 2:
                print("    Relaunching browser + 60s cooldown...")
                try: browser.close()
                except: pass
                time.sleep(60)
                browser, page = launch_browser(pw)
                consecutive_errors = 0
            return False

    for i, (from_d, to_d) in enumerate(chunks):
        if i < last_chunk:
            continue

        if i > last_chunk:
            time.sleep(60)  # long delay to avoid IP block

        success = do_chunk(i, from_d, to_d)
        if not success:
            failed_chunks.append((i, from_d, to_d))

        # Save after every chunk
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(all_incidents)
        progress[year_key] = i + 1
        save_progress(progress, year)

    # Retry failed chunks (up to 3 passes)
    for retry_pass in range(3):
        if not failed_chunks:
            break
        print(f"\n  Retry pass {retry_pass + 1}: {len(failed_chunks)} failed chunks")
        print(f"  Waiting 120s for rate limit to clear...")
        time.sleep(120)
        try: browser.close()
        except: pass
        browser, page = launch_browser(pw)

        still_failed = []
        for i, from_d, to_d in failed_chunks:
            time.sleep(15)
            success = do_chunk(i, from_d, to_d, is_retry=True)
            if not success:
                still_failed.append((i, from_d, to_d))
            # Save after each retry
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(all_incidents)
        failed_chunks = still_failed

    progress[year_key] = "done"
    save_progress(progress, year)
    try: browser.close()
    except: pass

    if failed_chunks:
        print(f"  {year} done with {len(failed_chunks)} unrecoverable failed chunks: {[(f, t) for _, f, t in failed_chunks]}")
    print(f"  {year} complete: {len(all_incidents)} shooting incidents")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=2018)
    parser.add_argument("--end", type=int, default=None)
    args = parser.parse_args()

    end_year = args.end or date.today().year
    sys.stdout.reconfigure(line_buffering=True)

    print(f"GVA Scraper (CSV Export) — {args.start} to {end_year}")
    print(f"Strategy: {CHUNK_DAYS}-day chunks, CSV export (no pagination)")
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
