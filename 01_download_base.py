"""
Download and filter the jamesqo gun-violence-data base dataset (Jan 2013 – Mar 2018).
Filters for incidents where n_killed + n_injured > 0 (someone was actually shot).
"""
import os, tarfile, io, requests, csv

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "base")
RAW_TAR = os.path.join(DATA_DIR, "DATA_01-2013_03-2018.tar.gz")
OUTPUT_CSV = os.path.join(DATA_DIR, "gva_2013_2018_shootings.csv")
DOWNLOAD_URL = "https://github.com/jamesqo/gun-violence-data/blob/master/DATA_01-2013_03-2018.tar.gz?raw=true"

def download():
    if os.path.exists(RAW_TAR):
        print(f"Already downloaded: {RAW_TAR}")
        return
    print("Downloading base dataset (~30MB)...")
    r = requests.get(DOWNLOAD_URL, stream=True)
    r.raise_for_status()
    with open(RAW_TAR, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"Saved to {RAW_TAR}")

def extract_and_filter():
    print("Extracting and filtering (n_killed + n_injured > 0)...")
    total = 0
    kept = 0
    with tarfile.open(RAW_TAR, "r:gz") as tar:
        # Find the CSV inside the tar
        csv_member = None
        for m in tar.getmembers():
            if m.name.endswith(".csv"):
                csv_member = m
                break
        if not csv_member:
            raise RuntimeError("No CSV found in tar.gz")

        f = tar.extractfile(csv_member)
        text = io.TextIOWrapper(f, encoding="utf-8")
        reader = csv.DictReader(text)

        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as out:
            writer = None
            for row in reader:
                total += 1
                killed = int(row.get("n_killed", 0) or 0)
                injured = int(row.get("n_injured", 0) or 0)
                if killed + injured > 0:
                    if writer is None:
                        writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
                        writer.writeheader()
                    writer.writerow(row)
                    kept += 1
                if total % 50000 == 0:
                    print(f"  Processed {total:,} rows, kept {kept:,}...")

    print(f"\nDone: {kept:,} / {total:,} incidents had casualties")
    print(f"Saved to {OUTPUT_CSV}")

if __name__ == "__main__":
    download()
    extract_and_filter()
