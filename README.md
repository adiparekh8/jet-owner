# Aircraft Owner Lookup

Type a US tail number (N-number) and see who the aircraft is registered to.
Data comes from the FAA's public Releasable Aircraft Database.

## Run

Needs Python 3. No other dependencies.

    python3 build_db.py
    python3 app.py

Then open http://localhost:8765.

`build_db.py` downloads the FAA registry file (about 70 MB) and loads it into
a SQLite database in `data/`. The FAA updates the file every night, so re-run
it to get fresh data. `python3 build_db.py --no-download` rebuilds from the
file already on disk.

Set the `PORT` environment variable to use a different port.

## Notes

- Only currently registered US aircraft are included.
- Street addresses are not stored or shown, only city and state.
- Many aircraft are registered to an LLC or a trust, so the registered owner
  is not always the person who uses the aircraft.
