"""Look up who owns an aircraft by its tail number, using the FAA registry.

Usage:
    python3 build_db.py     (once, and again whenever you want fresh data)
    python3 app.py          then open http://localhost:8765
"""

import html
import os
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

PORT = int(os.environ.get("PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "data", "aircraft.db")

OWNER_TYPES = {
    "1": "Individual",
    "2": "Partnership",
    "3": "Corporation",
    "4": "Co-owned",
    "5": "Government",
    "7": "LLC",
    "8": "Non-citizen corporation",
    "9": "Non-citizen co-owned",
}

AIRCRAFT_TYPES = {
    "1": "Glider",
    "2": "Balloon",
    "3": "Blimp / dirigible",
    "4": "Fixed wing, single engine",
    "5": "Fixed wing, multi engine",
    "6": "Rotorcraft",
    "7": "Weight-shift control",
    "8": "Powered parachute",
    "9": "Gyroplane",
    "H": "Hybrid lift",
    "O": "Other",
}

ENGINE_TYPES = {
    "0": "None",
    "1": "Reciprocating (piston)",
    "2": "Turbo-prop",
    "3": "Turbo-shaft",
    "4": "Turbo-jet",
    "5": "Turbo-fan",
    "6": "Ramjet",
    "7": "2 cycle",
    "8": "4 cycle",
    "9": "Unknown",
    "10": "Electric",
    "11": "Rotary",
}
JET_ENGINES = ("4", "5", "6")

STATUS = {
    "A": "Triennial form mailed, not returned",
    "D": "Expired dealer",
    "E": "Revoked by enforcement action",
    "M": "Valid (registered to manufacturer under dealer certificate)",
    "N": "Non-citizen corporation, flight hour reports not returned",
    "R": "Registration pending",
    "S": "Second triennial form mailed, not returned",
    "T": "Valid (trainee)",
    "V": "Valid",
    "W": "Certificate deemed ineffective or invalid",
    "X": "Enforcement letter",
    "Z": "Permanently reserved",
    "1": "Triennial form returned as undeliverable",
    "2": "N-number assigned, not yet registered",
    "3": "N-number assigned (non type certificated), not yet registered",
    "4": "N-number assigned (import), not yet registered",
    "5": "Reserved N-number",
    "6": "Administratively canceled",
    "7": "Sale reported",
    "8": "Second triennial mailing attempt, no response",
    "9": "Registration revoked",
    "10": "N-number assigned, not registered, pending cancellation",
    "11": "N-number assigned (amateur), not registered, pending cancellation",
    "12": "N-number assigned (import), not registered, pending cancellation",
    "13": "Registration expired",
    "14": "First notice for re-registration/renewal",
    "15": "Second notice for re-registration/renewal",
    "16": "Registration expired, pending cancellation",
    "17": "Sale reported, pending cancellation",
    "18": "Sale reported, canceled",
    "19": "Registration pending, pending cancellation",
    "20": "Registration pending, canceled",
    "21": "Revoked, pending cancellation",
    "22": "Revoked, canceled",
    "23": "Expired dealer, pending cancellation",
    "24": "Third notice for re-registration/renewal",
    "25": "First notice for registration renewal",
    "26": "Second notice for registration renewal",
    "27": "Registration expired",
    "28": "Third notice for registration renewal",
    "29": "Registration expired, pending cancellation",
}

# N-numbers: 1 to 5 characters after the N, starting with a digit 1-9,
# ending with up to two letters, never I or O.
VALID_N = re.compile(r"^[1-9](\d{0,4}|\d{0,3}[A-HJ-NP-Z]|\d{0,2}[A-HJ-NP-Z]{2})$")


def normalize(text):
    """Turn what the user typed into the registry's form (no leading N)."""
    s = re.sub(r"[^A-Z0-9]", "", text.upper())
    if s.startswith("N"):
        s = s[1:]
    return s


def fmt_date(d):
    if len(d) == 8 and d.isdigit():
        return "%s-%s-%s" % (d[0:4], d[4:6], d[6:8])
    return d


def fmt_int(s):
    return str(int(s)) if s.isdigit() else s


def lookup(n):
    db = sqlite3.connect("file:%s?mode=ro" % quote(DB_PATH), uri=True)
    db.row_factory = sqlite3.Row
    try:
        return db.execute("SELECT * FROM aircraft WHERE n_number = ?", (n,)).fetchone()
    finally:
        db.close()


def meta():
    db = sqlite3.connect("file:%s?mode=ro" % quote(DB_PATH), uri=True)
    try:
        return dict(db.execute("SELECT key, value FROM meta").fetchall())
    finally:
        db.close()


def page(query, body):
    e = html.escape
    m = meta()
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aircraft Owner Lookup</title>
</head>
<body>
<h1>Aircraft Owner Lookup</h1>
<p>Enter a US tail number (N-number) to see who it is registered to.</p>
<form method="get" action="/">
<label for="n">Tail number:</label>
<input type="text" id="n" name="n" value="%s" size="10" maxlength="12" autofocus>
<input type="submit" value="Look up">
</form>
%s
<hr>
<p><small>Source: <a href="https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download">FAA Releasable Aircraft Database</a>.
%s aircraft, data downloaded %s.
Only US-registered (N-number) aircraft are included.</small></p>
</body>
</html>
""" % (e(query), body, e("{:,}".format(int(m.get("count", "0")))), e(m.get("data_date", "unknown")))


def row_html(label, value):
    if not value:
        return ""
    return "<tr><th align=\"left\" valign=\"top\">%s</th><td>%s</td></tr>\n" % (
        html.escape(label),
        value,
    )


def result_html(query):
    e = html.escape
    n = normalize(query)
    if not n:
        return "<p><b>Please enter a tail number.</b></p>"
    faa_url = "https://registry.faa.gov/AircraftInquiry/Search/NNumberResult?nNumberTxt=N" + quote(n)
    if not VALID_N.match(n):
        return (
            "<p><b>N%s is not a valid US tail number.</b></p>"
            "<p>A tail number is N followed by 1 to 5 characters: digits, optionally "
            "ending in one or two letters (not I or O). Examples: N1, N123AB, N5127K.</p>" % e(n)
        )
    r = lookup(n)
    if r is None:
        return (
            "<p><b>N%s was not found.</b></p>"
            "<p>It is not in the FAA's list of currently registered aircraft. The number may be "
            "unassigned, reserved, or deregistered. "
            "<a href=\"%s\">Check the FAA site</a>.</p>" % (e(n), e(faa_url))
        )

    out = ["<h2>N%s</h2>\n" % e(r["n_number"])]

    owner = r["owner"]
    out.append("<table border=\"1\" cellpadding=\"4\" cellspacing=\"0\">\n")
    out.append(row_html("Registered owner", "<b>%s</b>" % e(owner) if owner else "<i>Not listed</i>"))
    out.append(row_html("Owner type", e(OWNER_TYPES.get(r["owner_type"], r["owner_type"]))))
    if r["other_names"]:
        out.append(row_html("Other owners", "<br>".join(e(x) for x in r["other_names"].split("\n"))))
    place = ", ".join(x for x in (r["city"], r["state"]) if x)
    if r["country"] and r["country"] != "US":
        place = ", ".join(x for x in (place, r["country"]) if x)
    out.append(row_html("Location", e(place)))
    if r["fractional"] == "Y":
        out.append(row_html("Fractional ownership", "Yes"))
    aircraft = " ".join(x for x in (r["manufacturer"], r["model"]) if x)
    out.append(row_html("Aircraft", e(aircraft)))
    out.append(row_html("Year built", e(r["year"])))
    out.append(row_html("Type", e(AIRCRAFT_TYPES.get(r["aircraft_type"], r["aircraft_type"]))))
    engine = ENGINE_TYPES.get(r["engine_type"], r["engine_type"])
    if r["engines"].isdigit() and int(r["engines"]) > 0 and r["engine_type"] != "0":
        engine = "%s x %s" % (int(r["engines"]), engine)
    out.append(row_html("Engines", e(engine)))
    if r["seats"].isdigit() and int(r["seats"]) > 0:
        out.append(row_html("Seats", e(fmt_int(r["seats"]))))
    out.append(row_html("Serial number", e(r["serial_number"])))
    out.append(row_html("Registration status", e(STATUS.get(r["status"], r["status"]))))
    out.append(row_html("Certificate issued", e(fmt_date(r["cert_issue_date"]))))
    out.append(row_html("Registration expires", e(fmt_date(r["expiration_date"]))))
    out.append(row_html("Last FAA action", e(fmt_date(r["last_action_date"]))))
    out.append(row_html("Mode S code (hex)", e(r["mode_s_hex"])))
    out.append("</table>\n")

    notes = []
    if r["engine_type"] not in JET_ENGINES:
        notes.append("This aircraft is not a jet.")
    if not owner:
        notes.append(
            "The FAA file lists no owner for this aircraft. The registration may be "
            "pending, or the owner may have asked the FAA to withhold their details."
        )
    elif r["owner_type"] != "1" or re.search(r"\b(TRUST|TRUSTEE|LLC|INC|CORP|BANK)\b", owner):
        notes.append(
            "The registered owner is a company or trust. Aircraft are often registered "
            "this way, so this may not be the person who actually uses it."
        )
    for note in notes:
        out.append("<p>Note: %s</p>\n" % e(note))
    out.append("<p><a href=\"%s\">Full record on the FAA site</a></p>\n" % e(faa_url))
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path != "/":
            self.respond(404, "<p><b>Page not found.</b> <a href=\"/\">Go back</a>.</p>", "")
            return
        params = parse_qs(url.query)
        if "n" in params:
            query = params["n"][0].strip()[:40]
            self.respond(200, result_html(query), query)
        else:
            self.respond(200, "", "")

    do_HEAD = do_GET

    def respond(self, status, body, query):
        try:
            data = page(query, body).encode("utf-8")
        except sqlite3.Error as err:
            status = 500
            data = ("<p>Database error: %s</p>" % html.escape(str(err))).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        sys.exit("No database found. Run this first:  python3 build_db.py")
    print("Serving on http://localhost:%d  (Ctrl+C to stop)" % PORT)
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        pass
