"""Download the FAA aircraft registry and load it into a SQLite database.

Usage:
    python3 build_db.py            download the latest file and rebuild
    python3 build_db.py --no-download   rebuild from the zip already on disk

The FAA refreshes the file every night, so run this once a day to stay current.
"""

import csv
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
ZIP_PATH = os.path.join(DATA_DIR, "ReleasableAircraft.zip")
DB_PATH = os.path.join(DATA_DIR, "aircraft.db")

# The FAA server refuses requests that do not look like a browser.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def download():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = ZIP_PATH + ".part"
    req = urllib.request.Request(URL, headers={"User-Agent": USER_AGENT})
    print("Downloading", URL)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if time.time() - last > 1:
                    last = time.time()
                    if total:
                        print("  %d / %d MB" % (done >> 20, total >> 20))
                    else:
                        print("  %d MB" % (done >> 20))
    except urllib.error.URLError as e:
        # Python installed from python.org on a Mac often has no root
        # certificates, so HTTPS fails. curl uses the system ones.
        if not shutil.which("curl"):
            sys.exit("Download failed: %s" % e)
        print("Python could not connect (%s); using curl instead" % e.reason)
        result = subprocess.run(
            ["curl", "-fL", "--retry", "3", "-A", USER_AGENT, "-o", tmp, URL]
        )
        if result.returncode != 0:
            sys.exit("Download failed (curl exit code %d)." % result.returncode)
    if not os.path.exists(tmp) or not zipfile.is_zipfile(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        sys.exit("Download failed: the FAA did not return a zip file. Try again later.")
    os.replace(tmp, ZIP_PATH)
    print("Saved", ZIP_PATH)


def read_table(zf, name):
    """Yield each row of a file in the zip as a dict keyed by column name."""
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.reader(text)
        header = [h.strip() for h in next(reader)]
        for row in reader:
            if not row:
                continue
            yield {h: v.strip() for h, v in zip(header, row) if h}


def need(row, *columns):
    missing = [c for c in columns if c not in row]
    if missing:
        sys.exit(
            "The FAA file is missing expected columns: %s\nColumns found: %s"
            % (", ".join(missing), ", ".join(row.keys()))
        )


def build():
    if not os.path.exists(ZIP_PATH):
        sys.exit("No data file at %s. Run without --no-download first." % ZIP_PATH)

    tmp_db = DB_PATH + ".new"
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    db = sqlite3.connect(tmp_db)
    db.executescript(
        """
        CREATE TABLE aircraft (
            n_number TEXT PRIMARY KEY,
            serial_number TEXT,
            owner TEXT,
            owner_type TEXT,
            other_names TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            manufacturer TEXT,
            model TEXT,
            year TEXT,
            aircraft_type TEXT,
            engine_type TEXT,
            engines TEXT,
            seats TEXT,
            status TEXT,
            fractional TEXT,
            cert_issue_date TEXT,
            expiration_date TEXT,
            last_action_date TEXT,
            mode_s_hex TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )

    with zipfile.ZipFile(ZIP_PATH) as zf:
        print("Reading ACFTREF.txt")
        models = {}
        checked = False
        for r in read_table(zf, "ACFTREF.txt"):
            if not checked:
                need(r, "CODE", "MFR", "MODEL", "NO-ENG", "NO-SEATS")
                checked = True
            models[r["CODE"]] = r

        print("Reading MASTER.txt")
        rows = []
        count = 0
        checked = False
        for r in read_table(zf, "MASTER.txt"):
            if not checked:
                need(
                    r, "N-NUMBER", "SERIAL NUMBER", "MFR MDL CODE", "YEAR MFR",
                    "TYPE REGISTRANT", "NAME", "CITY", "STATE", "COUNTRY",
                    "LAST ACTION DATE", "CERT ISSUE DATE", "TYPE AIRCRAFT",
                    "TYPE ENGINE", "STATUS CODE", "EXPIRATION DATE",
                )
                checked = True
            m = models.get(r["MFR MDL CODE"], {})
            others = [r.get("OTHER NAMES(%d)" % i, "") for i in range(1, 6)]
            rows.append((
                r["N-NUMBER"],
                r["SERIAL NUMBER"],
                r["NAME"],
                r["TYPE REGISTRANT"],
                "\n".join(o for o in others if o),
                r["CITY"],
                r["STATE"],
                r["COUNTRY"],
                m.get("MFR", ""),
                m.get("MODEL", ""),
                r["YEAR MFR"],
                r["TYPE AIRCRAFT"],
                r["TYPE ENGINE"],
                m.get("NO-ENG", ""),
                m.get("NO-SEATS", ""),
                r["STATUS CODE"],
                r.get("FRACT OWNER", ""),
                r["CERT ISSUE DATE"],
                r["EXPIRATION DATE"],
                r["LAST ACTION DATE"],
                r.get("MODE S CODE HEX", ""),
            ))
            if len(rows) >= 20000:
                db.executemany("INSERT OR REPLACE INTO aircraft VALUES (%s)" % ",".join("?" * 21), rows)
                count += len(rows)
                rows = []
        db.executemany("INSERT OR REPLACE INTO aircraft VALUES (%s)" % ",".join("?" * 21), rows)
        count += len(rows)

    file_date = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(ZIP_PATH)))
    db.execute("INSERT INTO meta VALUES ('data_date', ?)", (file_date,))
    db.execute("INSERT INTO meta VALUES ('count', ?)", (str(count),))
    db.commit()
    db.close()
    os.replace(tmp_db, DB_PATH)
    print("Loaded %d aircraft into %s" % (count, DB_PATH))


if __name__ == "__main__":
    if "--no-download" not in sys.argv:
        download()
    build()
