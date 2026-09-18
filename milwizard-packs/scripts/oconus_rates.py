#!/usr/bin/env python3
"""
oconus_rates.py — DTMO OCONUS per diem PDF -> Mil Wizard pack block (ported unchanged from Drill Wizard).

Reads DTMO's Current_OCONUS_Rates.pdf (a stable URL that always points at the current month),
parses every rate row, and writes the `oconus_mie` block into latest.json in the exact shape the
app compiles today:

    { "as_of": "2026-09-01",
      "source": "DTMO OCONUS per diem supplement (travel.dod.mil), eff. 01 Sep 2026",
      "countries": { "AFGHANISTAN": [["HERAT (NON-US FACILITIES)", 40], ["HERAT", 28], ...],
                     "ALASKA": [["ADAK", 143], ...], ... } }

M&IE = local meal rate + local incidental rate.  A location with more than one season collapses
to [name, low, high] only when the seasons' M&IE actually differ; otherwise it is one row.

FAIL-CLOSED. Any anomaly — a row that will not parse, a country that cannot be split from its
location, too few countries or rows, a rate out of range, a header without an effective date —
exits non-zero and writes nothing.  The app keeps the last good table and its rates clock says
so honestly.  A red run is the signal; read the step log, not the banner.

Usage:  python3 oconus_rates.py <pdf-path> <latest.json-path>
        python3 oconus_rates.py --self-test
"""
import json, re, sys, datetime

PDF_URL = "https://www.travel.dod.mil/Portals/119/Documents/Allowances/Per_Diem/OCONUS/Current_OCONUS_Rates.pdf"

# One rate row. Country and location share the leading text; the numeric tail is fixed-width in
# count except for the optional footnote number (present on maybe one row in twenty).
ROW = re.compile(
    r"^(?P<lead>.+?)\s+"
    r"(?P<b>\d\d/\d\d)\s+(?P<e>\d\d/\d\d)\s+"
    r"(?P<lodging>\d+)\s+(?P<meal>\d+)\s+(?P<prop>\d+)\s+(?P<inc>\d+)\s+"
    r"(?:(?P<fn>\d+)\s+)?"
    r"(?P<max>\d+)\s+(?P<eff>\d\d/\d\d/\d{4})\s*$"
)
HEADER = re.compile(r"EFFECTIVE:\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
MONTHS = {m: i for i, m in enumerate(
    ["January","February","March","April","May","June","July","August","September","October","November","December"], 1)}


def split_country(lead, known, prev_country):
    """Country is the longest known name that prefixes the lead text on a word boundary; failing
    that, a lead of the form 'X X' (single-location countries) is country X; failing that, the
    previous row's country if it still prefixes.  Anything else is an error, on purpose."""
    best = None
    for c in known:
        if lead == c or lead.startswith(c + " "):
            if best is None or len(c) > len(best):
                best = c
    if best is not None:
        loc = lead[len(best):].strip()
        return best, (loc if loc else best)
    half = (len(lead) + 1) // 2
    if len(lead) % 2 == 1 and lead[:half - 1] == lead[half:]:
        return lead[:half - 1], lead[half:]
    if prev_country and (lead == prev_country or lead.startswith(prev_country + " ")):
        loc = lead[len(prev_country):].strip()
        return prev_country, (loc if loc else prev_country)
    raise ValueError("cannot split country from location: %r" % lead)


def parse(text, known_countries):
    lines = [l.strip() for l in text.splitlines()]
    m = None
    for l in lines:
        m = HEADER.search(l)
        if m:
            break
    if not m:
        raise ValueError("no EFFECTIVE: header found")
    day, mon, year = int(m.group(1)), MONTHS.get(m.group(2).capitalize()), int(m.group(3))
    if not mon:
        raise ValueError("unrecognised month in header: %r" % m.group(2))
    as_of = "%04d-%02d-%02d" % (year, mon, day)
    source = "DTMO OCONUS per diem supplement (travel.dod.mil), eff. %02d %s %d" % (day, m.group(2)[:3], year)

    seasons = {}      # (country, location) -> list of M&IE in row order
    order = []        # first-seen order of (country, location)
    prev = None
    rows = 0
    for l in lines:
        r = ROW.match(l)
        if not r:
            continue
        country, loc = split_country(r.group("lead"), known_countries, prev)
        prev = country
        mie = int(r.group("meal")) + int(r.group("inc"))
        if not (0 <= mie <= 600):
            raise ValueError("M&IE out of range on %r: %d" % (l, mie))
        key = (country, loc)
        if key not in seasons:
            seasons[key] = []
            order.append(key)
        seasons[key].append(mie)
        rows += 1

    countries = {}
    for key in order:
        c, loc = key
        vals = seasons[key]
        lo, hi = min(vals), max(vals)
        countries.setdefault(c, []).append([loc, lo] if lo == hi else [loc, lo, hi])

    # the gates: sized against the table as it has looked for years
    if len(countries) < 200:
        raise ValueError("only %d countries parsed (expected 200+)" % len(countries))
    if rows < 1300:
        raise ValueError("only %d rate rows parsed (expected 1300+)" % rows)
    for must in ["ALASKA", "HAWAII", "GERMANY", "JAPAN", "PUERTO RICO", "GUAM", "KOREA, SOUTH", "UNITED KINGDOM"]:
        if must not in countries:
            raise ValueError("expected country missing: %s" % must)
    other = sum(1 for c in countries.values() if any(r[0] == "[OTHER]" for r in c))
    if other < 150:
        raise ValueError("only %d countries carry an [OTHER] row (expected 150+)" % other)
    return {"as_of": as_of, "source": source, "countries": countries}, rows


def merge_into_latest(block, path):
    """Write the block into every pack in latest.json, bump `generated`, and report whether
    anything changed.  Only the oconus_mie key and `generated` are touched."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    before = json.dumps(doc, sort_keys=True)
    packs = doc.get("packs") or []
    if not packs:
        raise ValueError("latest.json carries no packs")
    changed = False
    for pk in packs:
        if pk.get("oconus_mie") != block:
            pk["oconus_mie"] = block
            changed = True
    if changed:
        today = datetime.date.today().isoformat()
        doc["generated"] = today
        for pk in packs:
            pk["generated"] = today
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, ensure_ascii=False)
            f.write("\n")
    return changed


# Known country names, from the app's compiled table.  The longest-prefix rule needs them because
# both halves of a row are free text in capitals.  New countries fall through to the two fallbacks.
KNOWN = ["AFGHANISTAN","ALASKA","ALBANIA","ALGERIA","ALL PLACES NOT LISTED","AMERICAN SAMOA","ANDORRA","ANGOLA","ANGUILLA",
"ANTARCTICA","ANTIGUA AND BARBUDA","ARGENTINA","ARMENIA","ARUBA","ASCENSION ISLAND","AUSTRALIA","AUSTRIA","AZERBAIJAN",
"BAHAMAS, THE","BAHRAIN","BANGLADESH","BARBADOS","BELARUS","BELGIUM","BELIZE","BENIN","BERMUDA","BHUTAN","BOLIVIA",
"BONAIRE, SINT EUSTATIUS, AND SABA","BOSNIA AND HERZEGOVINA","BOTSWANA","BRAZIL","BRUNEI","BULGARIA","BURKINA FASO","BURMA",
"BURUNDI","CABO VERDE","CAMBODIA","CAMEROON","CANADA","CAYMAN ISLANDS","CENTRAL AFRICAN REPUBLIC","CHAD","CHAGOS ARCHIPELAGO",
"CHILE","CHINA","COCOS (KEELING) ISLANDS","COLOMBIA","COMOROS","COOK ISLANDS","COSTA RICA","COTE D'IVOIRE","CROATIA","CUBA",
"CURACAO","CYPRUS","CZECHIA","DEM. PEOPLE'S REPUBLIC OF KOREA","DEMOCRATIC REPUBLIC OF THE CONGO","DENMARK","DJIBOUTI",
"DOMINICA","DOMINICAN REPUBLIC","DUTCH CARIBBEAN","ECUADOR","EGYPT","EL SALVADOR","EQUATORIAL GUINEA","ERITREA","ESTONIA",
"ESWATINI","ETHIOPIA","FALKLAND ISLANDS (ISLAS MALVINAS)","FAROE ISLANDS","FIJI","FINLAND","FRANCE","FRENCH GUIANA",
"FRENCH POLYNESIA","GABON","GAMBIA, THE","GEORGIA","GERMANY","GHANA","GIBRALTAR","GREECE","GREENLAND","GRENADA","GUADELOUPE",
"GUAM","GUATEMALA","GUINEA-BISSAU","GUINEA","GUYANA","HAITI","HAWAII","HOLY SEE","HONDURAS","HONG KONG","HUNGARY","ICELAND",
"INDIA","INDONESIA","IRAN","IRAQ","IRELAND","ISRAEL","ITALY","JAMAICA","JAPAN","JORDAN","KAZAKHSTAN","KENYA","KIRIBATI",
"KOREA, SOUTH","KOSOVO","KUWAIT","KYRGYZSTAN","LAOS","LATVIA","LEBANON","LESOTHO","LIBERIA","LIBYA","LIECHTENSTEIN",
"LITHUANIA","LUXEMBOURG","MACAU","MADAGASCAR","MALAWI","MALAYSIA","MALDIVES","MALI","MALTA","MARSHALL ISLANDS","MARTINIQUE",
"MAURITANIA","MAURITIUS","MAYOTTE","MEXICO","MICRONESIA, FEDERATED STATES OF","MIDWAY ISLANDS","MOLDOVA","MONACO",
"MONGOLIA","MONTENEGRO","MONTSERRAT","MOROCCO","MOZAMBIQUE","NAMIBIA","NAURU","NEPAL","NETHERLANDS","NEW CALEDONIA",
"NEW ZEALAND","NICARAGUA","NIGERIA","NIGER","NIUE","NORTH MACEDONIA","NORTHERN MARIANA ISLANDS","NORWAY","OMAN",
"OTHER FOREIGN LOCALITIES","PAKISTAN","PALAU","PANAMA","PAPUA NEW GUINEA","PARAGUAY","PERU","PHILIPPINES","POLAND","PORTUGAL",
"PUERTO RICO","QATAR","REPUBLIC OF THE CONGO","RESERVE COMPONENT","REUNION","ROMANIA","RUSSIA","RWANDA","SAINT HELENA",
"SAINT KITTS AND NEVIS","SAINT VINCENT AND THE GRENADINES","SAMOA","SAN MARINO","SAO TOME AND PRINCIPE","SAUDI ARABIA",
"SENEGAL","SERBIA","SEYCHELLES","SIERRA LEONE","SINGAPORE","SINT MAARTEN","SLOVAKIA","SLOVENIA","SOLOMON ISLANDS","SOMALIA",
"SOUTH AFRICA","SOUTH SUDAN","SPAIN","SRI LANKA","ST LUCIA","SUDAN","SURINAME","SWEDEN","SWITZERLAND","SYRIA","TAIWAN",
"TAJIKISTAN","TANZANIA","THAILAND","TIMOR-LESTE","TOGO","TOKELAU","TONGA","TRINIDAD AND TOBAGO","TUNISIA","TURKEY",
"TURKMENISTAN","TURKS AND CAICOS ISLANDS","TUVALU","UGANDA","UKRAINE","UNITED ARAB EMIRATES","UNITED KINGDOM","URUGUAY",
"UZBEKISTAN","VANUATU","VENEZUELA","VIETNAM","VIRGIN ISLANDS (U.S.)","VIRGIN ISLANDS, BRITISH","WAKE ISLAND",
"WALLIS AND FUTUNA","YEMEN","ZAMBIA","ZIMBABWE"]


SELF_TEST = """EFFECTIVE: 01 September 2026 MAXIMUM PER DIEM RATES OUTSIDE THE CONTINENTAL UNITED STATES
AFGHANISTAN HERAT (NON-US FACILITIES) 01/01 12/31 99 32 25 8 1 139 12/01/2015
AFGHANISTAN HERAT 01/01 12/31 0 22 20 6 1 28 06/01/2011
AFGHANISTAN [OTHER] 01/01 12/31 0 12 15 3 1 15 08/01/2003
ALASKA ADAK 01/01 12/31 239 114 66 29 382 01/01/2026
ALASKA ANCHORAGE 04/01 09/30 329 118 68 30 477 01/01/2026
ALASKA ANCHORAGE 10/01 03/31 239 118 68 30 387 01/01/2026
ALASKA PRUDHOE BAY 01/01 12/31 239 114 66 29 3 382 01/01/2026
ALASKA [OTHER] 01/01 12/31 239 114 66 29 382 01/01/2026
ALL PLACES NOT LISTED ALL PLACES NOT LISTED 01/01 12/31 55 34 26 9 98 10/01/2024
ANDORRA ANDORRA 01/01 12/31 283 102 60 26 411 03/01/2026
ANTIGUA AND BARBUDA ANTIGUA AND BARBUDA 01/01 06/01 346 111 65 27 484 04/01/2023
ANTIGUA AND BARBUDA ANTIGUA AND BARBUDA 06/02 12/31 216 100 59 25 341 04/01/2023
ANTIGUA AND BARBUDA [OTHER] 04/16 12/14 37 15 17 3 55 05/01/2008
ANTIGUA AND BARBUDA [OTHER] 12/15 04/15 50 16 17 3 69 05/01/2008
BAHAMAS, THE ELEUTHERA ISLAND 04/17 11/14 213 114 66 29 356 09/01/2015
BAHAMAS, THE ELEUTHERA ISLAND 11/15 04/16 276 120 69 30 426 09/01/2015
GUINEA-BISSAU BISSAU 01/01 12/31 160 64 41 16 240 08/01/2026
GUINEA CONAKRY 01/01 12/31 224 75 47 19 318 06/01/2022
KOREA, SOUTH CAMP HUMPHREYS 01/01 12/31 66 41 30 10 117 09/01/2026
NIGERIA ABUJA 01/01 12/31 321 96 57 23 440 03/01/2025
NIGER NIAMEY 01/01 12/31 177 67 43 17 261 01/01/2025
RESERVE COMPONENT NO PER DIEM LOCATION (RC) 01/01 12/31 0 0 0 0 27 0 07/01/2012
VIRGIN ISLANDS (U.S.) ST. CROIX 07/01 10/31 247 92 55 23 362 10/01/2024
VIRGIN ISLANDS (U.S.) ST. CROIX 11/01 06/30 299 92 55 23 414 10/01/2024
VIRGIN ISLANDS, BRITISH VIRGIN ISLANDS, BRITISH 04/15 12/14 138 80 49 20 238 08/01/2010
ZIMBABWE VICTORIA FALLS 07/01 11/30 363 92 55 23 478 02/01/2025
ZIMBABWE VICTORIA FALLS 12/01 06/30 290 86 52 22 398 02/01/2025
"""


def self_test():
    # the gates are sized for the real table; test the parser body alone
    lines = [l.strip() for l in SELF_TEST.splitlines()]
    prev = None; got = {}; order = []
    for l in lines:
        r = ROW.match(l)
        if not r: continue
        c, loc = split_country(r.group("lead"), KNOWN, prev); prev = c
        got.setdefault((c, loc), []).append(int(r.group("meal")) + int(r.group("inc")))
    def one(c, loc):
        v = got[(c, loc)]; return [loc, min(v)] if min(v) == max(v) else [loc, min(v), max(v)]
    checks = [
        (one("AFGHANISTAN", "HERAT (NON-US FACILITIES)"), ["HERAT (NON-US FACILITIES)", 40]),   # footnote column present
        (one("AFGHANISTAN", "HERAT"), ["HERAT", 28]),
        (one("ALASKA", "ADAK"), ["ADAK", 143]),
        (one("ALASKA", "ANCHORAGE"), ["ANCHORAGE", 148]),                                      # two seasons, same M&IE -> one row
        (one("ALASKA", "PRUDHOE BAY"), ["PRUDHOE BAY", 143]),                                  # footnote on a non-foreign row
        (one("ALL PLACES NOT LISTED", "ALL PLACES NOT LISTED"), ["ALL PLACES NOT LISTED", 43]),
        (one("ANDORRA", "ANDORRA"), ["ANDORRA", 128]),
        (one("ANTIGUA AND BARBUDA", "ANTIGUA AND BARBUDA"), ["ANTIGUA AND BARBUDA", 125, 138]),  # seasons differ -> [low, high]
        (one("BAHAMAS, THE", "ELEUTHERA ISLAND"), ["ELEUTHERA ISLAND", 143, 150]),
        (one("GUINEA-BISSAU", "BISSAU"), ["BISSAU", 80]),                                      # longest prefix beats GUINEA
        (one("GUINEA", "CONAKRY"), ["CONAKRY", 94]),
        (one("KOREA, SOUTH", "CAMP HUMPHREYS"), ["CAMP HUMPHREYS", 51]),
        (one("NIGERIA", "ABUJA"), ["ABUJA", 119]),                                             # NIGERIA is not NIGER + IA
        (one("NIGER", "NIAMEY"), ["NIAMEY", 84]),
        (one("RESERVE COMPONENT", "NO PER DIEM LOCATION (RC)"), ["NO PER DIEM LOCATION (RC)", 0]),
        (one("VIRGIN ISLANDS (U.S.)", "ST. CROIX"), ["ST. CROIX", 115]),
        (one("VIRGIN ISLANDS, BRITISH", "VIRGIN ISLANDS, BRITISH"), ["VIRGIN ISLANDS, BRITISH", 100]),
        (one("ZIMBABWE", "VICTORIA FALLS"), ["VICTORIA FALLS", 108, 115]),
    ]
    bad = [(g, w) for g, w in checks if g != w]
    for g, w in bad:
        print("  MISMATCH got %r want %r" % (g, w))
    m = HEADER.search(SELF_TEST)
    ok = not bad and m and m.group(2) == "September"
    print("self-test: %d/%d rows as expected, header %s" % (len(checks) - len(bad), len(checks), "ok" if m else "MISSING"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    if len(argv) != 3:
        print(__doc__); return 2
    pdf_path, latest_path = argv[1], argv[2]
    import pdfplumber
    text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text.append(page.extract_text() or "")
    block, rows = parse("\n".join(text), KNOWN)
    print("parsed %d rows into %d countries, effective %s" % (rows, len(block["countries"]), block["as_of"]))
    changed = merge_into_latest(block, latest_path)
    print("latest.json: %s" % ("UPDATED" if changed else "unchanged (already current)"))
    with open("oconus_result.txt", "w") as f:
        f.write("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
