#!/usr/bin/env python3
"""Mil Wizard TSP price updater — ported from Drill Wizard's v2 (first Run found the legacy endpoint dead).

v1 fetched one hardcoded tsp.gov URL; the first live Run died in the connection phase,
which means that address no longer answers. v2 tries a short list of candidate endpoints
in order with a normal browser identity, parses each answer with the same fail-closed
parser, takes the first that yields sane prices, and — if none do — prints ONE line per
candidate saying exactly how it failed, then exits non-zero having written nothing.

Doctrine, unchanged:
  * HEADER-NAME-KEYED — columns found by header text, never by position.
  * FAIL-CLOSED — anomalies write nothing and fail the job loudly.
  * IDEMPOTENT — an unchanged as_of exits 0 without touching latest.json.

`python3 update_tsp_prices.py --selftest` proves the parser with no network.
"""
import csv, io, json, re, sys, datetime, urllib.request

# Tried in order. {s}/{e} are start/end dates rendered in each candidate's own format.
CANDIDATES = [
    ("www.tsp.gov fund-price-history CSV",
     "https://www.tsp.gov/data/fund-price-history.csv?startdate={s}&enddate={e}&Lfunds=1&InvFunds=1&download=1",
     "%Y-%m-%d"),
    ("www.tsp.gov fund-price-history CSV (no download flag)",
     "https://www.tsp.gov/data/fund-price-history.csv?startdate={s}&enddate={e}&Lfunds=1&InvFunds=1",
     "%Y-%m-%d"),
    ("legacy secure.tsp.gov getSharePricesRaw",
     "https://secure.tsp.gov/components/CORS/getSharePricesRaw.html?startdate={s}&enddate={e}&Lfunds=1&InvFunds=1&format=CSV&download=0",
     "%m/%d/%Y"),
]
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/csv,text/plain,*/*",
}
LATEST = "latest.json"
STALE_DAYS = 10

HEADER_TO_ID = {
    "G FUND": "G", "F FUND": "F", "C FUND": "C", "S FUND": "S", "I FUND": "I",
    "L INCOME": "LI", "L 2030": "L30", "L 2035": "L35", "L 2040": "L40",
    "L 2045": "L45", "L 2050": "L50", "L 2055": "L55", "L 2060": "L60",
    "L 2065": "L65", "L 2070": "L70", "L 2075": "L75",
}
CORE = {"G", "F", "C", "S", "I"}

def norm(h):
    return re.sub(r"\s+", " ", (h or "").strip()).upper()

def parse_prices(text):
    """CSV text -> (as_of_iso, {fund_id: price}). Raises on anything suspicious."""
    if text.lstrip()[:1] == "<":
        raise ValueError("answer was HTML, not CSV — likely a block/redirect page")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("feed had no data rows")
    header = [norm(c) for c in rows[0]]
    if "DATE" not in header:
        raise ValueError("no Date column in header: %r" % header[:6])
    di = header.index("DATE")
    cols = {i: HEADER_TO_ID[h] for i, h in enumerate(header) if h in HEADER_TO_ID}
    if not cols:
        raise ValueError("no recognisable fund columns in header: %r" % header)

    def parse_date(s):
        s = (s or "").strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        raise ValueError("unreadable date: %r" % s)

    dated = []
    for r in rows[1:]:
        if len(r) <= di or not (r[di] or "").strip():
            continue
        dated.append((parse_date(r[di]), r))
    if not dated:
        raise ValueError("no dated rows")
    dated.sort(key=lambda x: x[0])
    day, row = dated[-1]

    px = {}
    for i, fid in cols.items():
        raw = (row[i] if i < len(row) else "").strip().replace("$", "").replace(",", "")
        if raw in ("", "-", "N/A"):
            continue
        v = float(raw)
        if not (0 < v < 1000):
            raise ValueError("implausible price for %s: %r" % (fid, raw))
        px[fid] = round(v, 4)
    missing = CORE - set(px)
    if missing:
        raise ValueError("core funds missing from the feed: %s" % sorted(missing))
    age = (datetime.date.today() - day).days
    if age > STALE_DAYS:
        raise ValueError("newest price is %d days old (%s) — refusing stale data" % (age, day))
    return day.isoformat(), px

def fetch_and_parse():
    end = datetime.date.today()
    start = end - datetime.timedelta(days=14)
    failures = []
    for label, tmpl, datefmt in CANDIDATES:
        url = tmpl.format(s=start.strftime(datefmt), e=end.strftime(datefmt))
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            as_of, px = parse_prices(body)
            print("source: %s (as_of %s, %d funds)" % (label, as_of, len(px)))
            return as_of, px
        except Exception as e:
            failures.append("  %s -> %s: %s" % (label, type(e).__name__, e))
    print("every candidate endpoint failed — nothing will be written:")
    for f in failures:
        print(f)
    raise SystemExit(1)

FIXTURE = """Date,L Income,L 2030,L 2035,L 2040,L 2045,L 2050,L 2055,L 2060,L 2065,L 2070,L 2075,G Fund,F Fund,C Fund,S Fund,I Fund
{d1},27.1035,52.9012,15.8873,60.1450,16.6021,36.5533,19.9902,19.9871,19.9840,12.4410,10.8801,19.1201,19.8804,111.2003,92.6644,49.3320
{d0},27.1201,52.9873,15.9114,60.2311,16.6302,36.6120,20.0411,20.0380,20.0349,12.4633,10.9002,19.1233,19.9011,111.9450,93.1002,49.5011
"""

def selftest():
    today = datetime.date.today()
    text = FIXTURE.format(d0=today.strftime("%m/%d/%Y"),
                          d1=(today - datetime.timedelta(days=1)).strftime("%m/%d/%Y"))
    as_of, px = parse_prices(text)
    assert as_of == today.isoformat(), as_of
    assert px["C"] == 111.945 and px["G"] == 19.1233 and px["LI"] == 27.1201, px
    assert set(px) == set(HEADER_TO_ID.values()), sorted(set(HEADER_TO_ID.values()) - set(px))
    rev = text.splitlines()
    as_of2, px2 = parse_prices("\n".join([rev[0], rev[2], rev[1]]) + "\n")
    assert (as_of2, px2) == (as_of, px)
    try:
        parse_prices(text.replace("C Fund", "X Fund"))
        raise SystemExit("selftest FAILED: a missing core fund was accepted")
    except ValueError:
        pass
    try:
        parse_prices("<html><body>blocked</body></html>")
        raise SystemExit("selftest FAILED: an HTML block page was accepted")
    except ValueError:
        pass
    print("selftest OK — %d funds, as_of %s, newest-row, fail-closed and HTML-guard proven" % (len(px), as_of))

def main():
    if "--selftest" in sys.argv:
        return selftest()
    as_of, px = fetch_and_parse()
    with open(LATEST, encoding="utf-8") as f:
        doc = json.load(f)
    packs = doc.get("packs") or []
    if not packs:
        raise SystemExit("latest.json has no packs — refusing to write")
    if all((p.get("tsp_prices") or {}).get("as_of") == as_of for p in packs):
        print("already at %s — nothing to do" % as_of)
        return
    today = datetime.date.today().isoformat()
    for p in packs:
        p["tsp_prices"] = {"as_of": as_of, "px": px}
        p["generated"] = today
    doc["generated"] = today
    with open(LATEST, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    print("wrote %d funds, as_of %s, into %d pack(s)" % (len(px), as_of, len(packs)))

if __name__ == "__main__":
    main()
