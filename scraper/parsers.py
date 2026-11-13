"""Lounaslistojen jäsentimet. Siirretty kokkola-lounas-repon update_menus.py:stä.

Jokainen parse_*-funktio palauttaa viikon listat muodossa {"0": [...], ..., "4": [...]}
(ma–pe). Toimipisteet ja ravintolat määritellään sites.json- ja restaurants.json-tiedostoissa.
"""
import io
import json
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
}

WEEKDAYS_FI = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai"]

# Näytetään, jos ravintolan päivän listaa ei saada haettua. Ei keksittyjä annoksia.
PLACEHOLDER_ITEM = {
    "title": "Päivän lista ei ole saatavilla – katso ravintolan omilta sivuilta",
    "diets": [],
    "price": None,
}

PANCHO_STATIC_FALLBACK = [
    "Panchon burgerit (juusto / pekoni / kana / valkosipuli / chili / kasvis) 14,00 €, tupla 17,50 € (gluteeniton pyydettäessä)",
    "Lounassalaatti (kana / halloumi / lohi) 14,50 €",
    "Lounaspihvi (paneroitu kana / paneroitu porsaan pihvi) 14,50 €",
]

# Merkinnät, jotka näytetään sivulla. Muut (A, ILM, VS, * ...) jätetään pois.
DIET_MAP = {
    "G": "G",
    "L": "L",
    "M": "M",
    "VL": "VL",
    "VEG": "Veg",
    "PG": "PG",
    "GV!": "PG",  # Harjun Rinkeli: "toteutettavissa gluteenittomana tilauksesta"
    "V": "Veg",
    "V*": "Veg",  # Smokey Road Diner
    "VE": "Veg",  # John Scott's, Robert's Coffee
    "VEGAANINEN": "Veg",  # Via Tribunali
}
DIET_ORDER = ["G", "PG", "L", "VL", "M", "Veg"]

DIET_TOKEN = r"(?:g|l|m|vl|veg|ve|pg|gv!|v\*?)"
# Rivin lopussa olevat merkinnät, esim. "g,l", "l gv!", "(L, PG)"
TRAILING_DIETS_RE = re.compile(
    rf"[\s,]*\(?\s*(?<![^\s,(])({DIET_TOKEN}(?:[\s,]+{DIET_TOKEN})*)\s*\)?\s*$", re.IGNORECASE
)
PAREN_DIETS_RE = re.compile(rf"\(\s*({DIET_TOKEN}(?:[\s,]+{DIET_TOKEN})*)\s*\)", re.IGNORECASE)
PRICE_RE = re.compile(r"(\d{1,2})(?:[.,](\d{1,2}))?\s*€")
# lounaat.infon hintakenttä ja vapaamuotoiset hinnat: "13,90e", "13 euro"
LOOSE_PRICE_RE = re.compile(r"(\d{1,2})(?:[.,](\d{1,2}))?\s*(?:€|e\b|eur\b|euroa?\b)", re.IGNORECASE)


def week_dates(today: date):
    monday = today - timedelta(days=today.weekday())
    return [monday + timedelta(days=i) for i in range(5)]


def empty_week() -> dict:
    return {str(i): [] for i in range(5)}


def has_any_items(menus: dict) -> bool:
    return bool(menus) and any(len(items) > 0 for items in menus.values())


def fetch_html(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding if not resp.encoding else resp.encoding
        return resp.text
    except Exception as e:
        print(f"  Virhe haettaessa {url}: {e}")
        return None


def fetch_json(url: str):
    try:
        resp = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Virhe haettaessa {url}: {e}")
        return None


def fetch_bytes(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"  Virhe haettaessa {url}: {e}")
        return None


def normalize_diets(tokens) -> list:
    found = set()
    for t in tokens:
        key = t.strip().upper()
        if key in DIET_MAP:
            found.add(DIET_MAP[key])
    if "G" in found:
        found.discard("PG")
    return [d for d in DIET_ORDER if d in found]


def format_price(match) -> str:
    euros, cents = match.group(1), match.group(2) or "00"
    return f"{euros},{cents.ljust(2, '0')} €"


def parse_dish_line(text: str):
    """Erottaa annosrivistä nimen, ruokavaliomerkit ja hinnan.

    Esim. "Rapea kana ja mangomajoneesi 13.40 € g,l" -> ("Rapea kana ja mangomajoneesi", ["G", "L"], "13,40 €")
    """
    text = re.sub(r"\s+", " ", text).strip()
    diets = []

    price = None
    pm = PRICE_RE.search(text)
    if pm:
        price = format_price(pm)
        text = (text[: pm.start()] + " " + text[pm.end():]).strip()

    # Merkinnät voivat olla hinnan jälkeen tai ennen sitä, joten poistetaan toistuvasti lopusta
    while True:
        m = TRAILING_DIETS_RE.search(text)
        if not m or m.start() == 0:
            break
        diets += re.split(r"[\s,]+", m.group(1))
        text = text[: m.start()].strip()

    for m in PAREN_DIETS_RE.finditer(text):
        diets += re.split(r"[\s,]+", m.group(1))
    text = PAREN_DIETS_RE.sub("", text)

    text = re.sub(r"\s{2,}", " ", text).strip(" -–,.")
    return text, normalize_diets(diets), price


def match_weekday(line: str):
    """Palauttaa viikonpäivän indeksin (0-4), jos rivi on pelkkä viikonpäivän otsikko."""
    lower = line.lower().strip(" .:-–")
    for idx, day_name in enumerate(WEEKDAYS_FI):
        if lower.startswith(day_name) and len(lower) < 25:
            return idx
    return None


# --------------------------------------------------------------------------
# Harjun Rinkeli (harjunleipomo.fi/lounas)
# --------------------------------------------------------------------------
def parse_harjun_rinkeli(today: date) -> dict:
    menus = empty_week()
    html = fetch_html("https://harjunleipomo.fi/lounas")
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")

    heading = None
    for h in soup.find_all(["h2", "h3"]):
        if re.search(r"Harjun\s+Rinkeli\s*-\s*lounasravintola", h.get_text(" "), re.IGNORECASE):
            heading = h
            break
    if heading is None:
        print("  Harjun Rinkelin osiota ei löytynyt")
        return menus

    parts = []
    for sib in heading.find_next_siblings():
        if sib.name in ("h2", "h3"):
            break
        parts.append(sib.get_text("\n"))
    lines = [l.strip() for l in "\n".join(parts).splitlines() if l.strip()]

    week_m = re.search(r"\(vk\.?\s*(\d{1,2})\)", " ".join(lines), re.IGNORECASE)
    if week_m and int(week_m.group(1)) != today.isocalendar()[1]:
        print(f"  Harjun Rinkelin lista on viikolta {week_m.group(1)}, ohitetaan")
        return menus

    current = None
    for line in lines:
        day_idx = match_weekday(line)
        if day_idx is not None:
            current = day_idx
            continue
        if current is None:
            continue
        lower = line.lower()
        if lower.startswith(("lounas tarjolla", "lounas sisältää", "lauantai", "sunnuntai")):
            break
        title, diets, price = parse_dish_line(line)
        if len(title) > 2:
            menus[str(current)].append({"title": title, "diets": diets, "price": price})
    return menus


# --------------------------------------------------------------------------
# lounaat.info (Ravintola Hilda)
# --------------------------------------------------------------------------
# Rivit, jotka eivät ole annoksia (maksutavat, puuttuva lista ym.)
LOUNAAT_INFO_SKIP = re.compile(
    r"^(meillä käy|maksutavat|lounaslista puuttuu|päivän lounaslista puuttuu|lounas kello|tervetuloa)"
    r"|^(epassi|smartum|edenred)\b",
    re.IGNORECASE,
)


# Lisätietorivit, jotka näytetään kortissa annosten sijaan
LOUNAAT_INFO_NOTE = re.compile(
    r"^(sisältää\b|lounas on tarjolla|lounasbuffet sisältää|huomio|klo \d)", re.IGNORECASE
)


def parse_lounaat_info(url: str, today: date, package_title: str = None):
    """lounaat.info-sivun viikkolista.

    package_title: jos annettu, päivän rivit kootaan yhdeksi kokonaisuudeksi (esim. buffet),
    jonka hinta poimitaan riviltä kuten "Koko setti 13 euro".

    Palauttaa (menus, info), jossa info on lista lisätietoriveistä
    (esim. "Sisältää: salaattipöytä, leipä, kahvi").
    """
    menus = empty_week()
    info = []
    html = fetch_html(url)
    if not html:
        return menus, info
    soup = BeautifulSoup(html, "html.parser")
    dates = week_dates(today)

    for item in soup.select("div.item"):
        header = item.select_one(".item-header h3")
        if not header:
            continue
        header_text = header.get_text(" ", strip=True)
        day_idx = match_weekday(header_text)
        if day_idx is None:
            continue
        # Otsikko on muotoa "Maanantaina 12.10." – varmistetaan, että päivä on tältä viikolta
        dm = re.search(r"(\d{1,2})\.(\d{1,2})\.", header_text)
        if dm:
            d = dates[day_idx]
            if (int(dm.group(1)), int(dm.group(2))) != (d.day, d.month):
                continue

        day_items = []
        package_price = None
        section = None  # väliotsikko, esim. "Buffet" tai "Tilauksesta"
        for li in item.select("li.menu-item"):
            price_el = li.select_one(".price")
            field_price = None
            if price_el:
                pm = LOOSE_PRICE_RE.search(price_el.get_text(" ", strip=True))
                field_price = format_price(pm) if pm else None
                price_el.decompose()
            dish = li.select_one(".dish") or li
            diets = normalize_diets(a.get_text(strip=True) for a in dish.select("a.diet"))
            for a in dish.select("a.diet"):
                a.decompose()
            text = re.sub(r"\s+", " ", dish.get_text(" ")).strip()
            if not text or LOUNAAT_INFO_SKIP.search(text):
                continue
            if LOUNAAT_INFO_NOTE.search(text):
                if text not in info:
                    info.append(text)
                continue

            if package_title:
                # "Kaikki 13 euro" / "Koko setti 13 euro!" -> kokonaisuuden hinta
                pm = LOOSE_PRICE_RE.search(text)
                if pm and re.search(r"(kaikki|koko|setti|hinta|lounas)", text, re.IGNORECASE):
                    package_price = format_price(pm)
                    continue
                title, extra_diets, _ = parse_dish_line(text)
                if title:
                    day_items.append({"title": title, "diets": normalize_diets(diets + extra_diets)})
                continue

            # Väliotsikko ("Buffet:", "Tilauksesta:") liitetään seuraavien annosten eteen
            if re.match(r"^[A-ZÅÄÖa-zåäö][\wåäöÅÄÖ /&-]{1,24}:$", text) and not diets and not field_price:
                section = text.rstrip(":").strip()
                continue

            title, extra_diets, price = parse_dish_line(text)
            diets = normalize_diets(diets + extra_diets)
            price = field_price or price
            if not title:
                continue

            # lounaat.infoon syötetyt jatkorivit yhdistetään edelliseen annokseen:
            # "Porsaanleike l" + "Pyydettäessä g"  ->  "Porsaanleike" (L, G pyydettäessä)
            # "Jauhelihamureke" + "Bolognesekastikkeessa" -> "Jauhelihamureke bolognesekastikkeessa"
            if day_items and title.lower() == "pyydettäessä":
                prev = day_items[-1]
                prev["diets"] = normalize_diets(prev["diets"] + ["PG" if d == "G" else d for d in diets])
                continue
            # Jatkorivi, jonka jokainen sana on inessiivissä: "Täyteläisessä sipulikastikkeessa"
            words = title.lower().split()
            if day_items and len(words) <= 3 and all(re.search(r"(ssa|ssä)$", w) for w in words):
                prev = day_items[-1]
                prev["title"] = f"{prev['title']} {title.lower()}"
                if diets:
                    prev["diets"] = [d for d in prev["diets"] if d in diets]
                continue

            if section:
                title = f"{section}: {title}"

            day_items.append({"title": title, "diets": diets, "price": price})

        if package_title and day_items:
            common = set.intersection(*(set(p["diets"]) for p in day_items))
            day_items = [{
                "title": package_title,
                "diets": [d for d in DIET_ORDER if d in common],
                "price": package_price,
                "parts": day_items,
            }]
        menus[str(day_idx)] = day_items
    if not package_title:
        tidy_lounaat_info(menus, info)
    return menus, info


TRAILING_NUMBER_PRICE_RE = re.compile(r"\s(\d{1,2})[,.](\d{2})$")
PRICE_NOTE_RE = re.compile(r"\bhinta(an)?\b", re.IGNORECASE)


def _is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


def tidy_lounaat_info(menus: dict, info: list):
    """Siistii lounaat.infoon vapaasti kirjoitettuja listoja.

    - "Pork ramen 12,90" -> hinta erilleen, "Vegan ramen veg" -> merkintä Veg
    - "LOHIKEITTO" + "Savustettua kermaista kalalientä ..." -> annos ja sen kuvaus
    - hintarivit ("Lounasbuffetin hinta 13,80 €") ja joka päivä toistuvat tekstit
      ilman merkintöjä ja hintaa siirretään lisätietoihin
    """
    def note(text):
        text = re.sub(r"\s{2,}", " ", text).strip()
        if text and text not in info:
            info.append(text)

    for key, items in menus.items():
        tidied = []
        for item in items:
            title = item["title"]
            m = TRAILING_NUMBER_PRICE_RE.search(title)
            if m:
                title = title[: m.start()].strip()
                item["price"] = item.get("price") or format_price(m)
            title, more_diets, _ = parse_dish_line(title)
            title = re.sub(r"\s*,?\s+k$", "", title).strip(" ,")
            item["title"] = title
            item["diets"] = normalize_diets(item["diets"] + more_diets)
            if not title:
                continue
            if PRICE_NOTE_RE.search(title) and item.get("price") and not item["diets"]:
                note(re.sub(r"\b(hinta(?:an)?)\s*\.?", rf"\1 {item['price']}.", title, count=1))
                continue
            if len(title) > 120 and not item["diets"] and not item.get("price"):
                note(title)
                continue
            tidied.append(item)
        # Isoilla kirjaimilla kirjoitettu annoksen nimi ja sitä seuraava kuvausrivi
        merged = []
        i = 0
        while i < len(tidied):
            item = tidied[i]
            nxt = tidied[i + 1] if i + 1 < len(tidied) else None
            if _is_caps(item["title"]) and not item["diets"] and nxt and not _is_caps(nxt["title"]):
                if re.search(r"\d", item["title"]):
                    note(item["title"].capitalize())
                    i += 1
                    continue
                merged.append({
                    "title": item["title"].capitalize(),
                    "diets": nxt["diets"],
                    "price": item.get("price") or nxt.get("price"),
                    "desc": nxt["title"],
                })
                i += 2
                continue
            merged.append(item)
            i += 1
        menus[key] = merged

    # Osa ravintoloista kirjoittaa koko viikon lisätiedot viimeisen päivän perään:
    # tavallista pidemmän viimeisen päivän ylimääräiset rivit ilman merkintöjä ja hintaa
    filled = [k for k in sorted(menus) if menus[k]]
    if len(filled) >= 3:
        last = filled[-1]
        others = sorted(len(menus[k]) for k in filled[:-1])
        usual = others[len(others) // 2]
        if len(menus[last]) > usual + 2:
            keep = menus[last][:usual]
            for it in menus[last][usual:]:
                if it["diets"] or it.get("price") or it.get("desc"):
                    keep.append(it)
                else:
                    note(it["title"])
            menus[last] = keep

    # Teksti, joka toistuu vähintään kolmena päivänä ilman merkintöjä ja hintaa, on lisätieto
    counts = {}
    for items in menus.values():
        for t in {it["title"] for it in items if not it["diets"] and not it.get("price") and not it.get("desc")}:
            counts[t] = counts.get(t, 0) + 1
    repeated = {t for t, n in counts.items() if n >= 3}
    for t in sorted(repeated, key=lambda t: min(
            (k, i) for k, items in menus.items() for i, it in enumerate(items) if it["title"] == t)):
        note(t)
    for key in menus:
        menus[key] = [it for it in menus[key] if it["title"] not in repeated]


# --------------------------------------------------------------------------
# Compass Group / Food & Co (Kulma, Coccolita)
# --------------------------------------------------------------------------
def parse_compass(url: str, today: date) -> dict:
    menus = empty_week()
    html = fetch_html(url)
    if not html:
        return menus

    m = re.search(r"window\.__INITIAL_MENU__\s*=\s*", html)
    if not m:
        print(f"  __INITIAL_MENU__-dataa ei löytynyt: {url}")
        return menus
    try:
        data, _ = json.JSONDecoder().raw_decode(html, m.end())
    except ValueError as e:
        print(f"  Menu-JSONin jäsennys epäonnistui ({url}): {e}")
        return menus

    dates = [d.isoformat() for d in week_dates(today)]
    for day in (data.get("weekMenu") or {}).get("menus", []):
        day_date = (day.get("date") or "")[:10]
        if day_date not in dates:
            continue
        day_idx = dates.index(day_date)

        items = []
        for pkg in sorted(day.get("menuPackages", []), key=lambda p: p.get("sortOrder", 0)):
            parts = []
            for meal in pkg.get("meals", []):
                raw = meal.get("name", "")
                diets = list(meal.get("diets") or [])
                # "(pyyd. Glut)" = saatavilla gluteenittomana pyydettäessä
                if re.search(r"\(\s*pyyd\.?\s*glut\.?\s*\)", raw, re.IGNORECASE):
                    raw = re.sub(r"\(\s*pyyd\.?\s*glut\.?\s*\)", "", raw, flags=re.IGNORECASE)
                    diets.append("PG")
                name, inline_diets, _ = parse_dish_line(raw)
                if name:
                    parts.append({"title": name, "diets": normalize_diets(diets + inline_diets)})
            if not parts:
                continue

            pkg_name = (pkg.get("name") or "").strip()
            price_text = pkg.get("price") or ""
            # "So Good Buffet 13,50€": hinta paketin nimessä
            name_price = PRICE_RE.search(pkg_name)
            if name_price:
                pkg_name = (pkg_name[: name_price.start()] + pkg_name[name_price.end():]).strip()
                price_text = price_text or name_price.group(0)
            pkg_name = pkg_name[:1].upper() + pkg_name[1:].lower()
            # Opiskelijaravintoloiden hinnat ("Opiskelija 3,10€ / henkilökunta 7,35€") eivät koske muita
            price_m = None if re.search(r"opiskelija", price_text, re.IGNORECASE) else PRICE_RE.search(price_text)
            # Kokonaisuuden jokainen osa näytetään omine merkintöineen (parts).
            # Kokonaisuuden "diets" = merkinnät, jotka sopivat kaikkiin osiin.
            common = set.intersection(*(set(p["diets"]) for p in parts))
            items.append({
                "title": pkg_name or "Lounas",
                "diets": [d for d in DIET_ORDER if d in common],
                "price": format_price(price_m) if price_m else None,
                "parts": parts,
            })
        menus[str(day_idx)] = items
    return menus


# --------------------------------------------------------------------------
# Sodexo (viikon JSON-rajapinta)
# --------------------------------------------------------------------------
def parse_sodexo(restaurant_id: int, today: date) -> dict:
    """Sodexon viikkolista. Hinnoista näytetään viimeinen eli ulkopuolisten hinta."""
    menus = empty_week()
    data = fetch_json(f"https://www.sodexo.fi/ruokalistat/output/weekly_json/{restaurant_id}")
    if not data:
        return menus
    # timeperiod "9.11. - 15.11." – varmistetaan, että lista on tältä viikolta
    monday = week_dates(today)[0]
    tp = re.match(r"\s*(\d{1,2})\.(\d{1,2})\.", data.get("timeperiod") or "")
    if tp and (int(tp.group(1)), int(tp.group(2))) != (monday.day, monday.month):
        print(f"  Sodexon lista ei ole tältä viikolta ({data.get('timeperiod')})")
        return menus
    for day in data.get("mealdates", []):
        idx = match_weekday(day.get("date") or "")
        if idx is None:
            continue
        items = []
        for course in (day.get("courses") or {}).values():
            title = (course.get("title_fi") or "").strip()
            if not title:
                continue
            prices = PRICE_RE.findall(course.get("price") or "")
            price = format_price(re.match(PRICE_RE, f"{prices[-1][0]},{prices[-1][1] or '00'} €")) if prices else None
            diets = normalize_diets(re.split(r"[\s,]+", course.get("dietcodes") or ""))
            category = re.sub(r"\s+", " ", re.sub(r"\b(uni|\d+)\b", "", course.get("category") or "", flags=re.IGNORECASE)).strip()
            dish = {"title": title, "diets": diets}
            items.append({"title": category or "Lounas", "diets": diets, "price": price, "parts": [dish]}
                         if category else {**dish, "price": price})
        menus[str(idx)] = items
    return menus


# --------------------------------------------------------------------------
# Pancho Villa
# --------------------------------------------------------------------------
def parse_pancho(url: str = "https://panchovilla.fi/ravintolat/kokkola/"):
    """Palauttaa (menus, staticNote)."""
    menus = empty_week()
    html = fetch_html(url)
    if not html:
        return menus, None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]

    mex_price = "13,00 €"
    for line in lines:
        pm = re.match(r"MEKSIKOLOUNAS\s+(.*)", line, re.IGNORECASE)
        if pm and PRICE_RE.search(pm.group(1)):
            mex_price = format_price(PRICE_RE.search(pm.group(1)))
            break

    # Lista toistuu sivulla (mobiili/työpöytä), joten otetaan kunkin rivin ensimmäinen esiintymä
    for idx, day_name in enumerate(WEEKDAYS_FI):
        for line in lines:
            dm = re.match(rf"{day_name}\s*[–\-:]\s*(.+)", line, re.IGNORECASE)
            if dm:
                title, diets, _ = parse_dish_line(dm.group(1))
                if title:
                    menus[str(idx)].append({
                        "title": f"Meksikolounas: {title}",
                        "diets": diets,
                        "price": mex_price,
                    })
                break

    special = None
    static = []
    for line in lines:
        upper = line.upper()
        if special is None and upper.startswith("SPECIAL BURGER"):
            rest = re.sub(r"^SPECIAL\s+BURGER\s*[–\-:]?\s*", "", line, flags=re.IGNORECASE)
            title, diets, price = parse_dish_line(rest)
            special = {"title": f"Viikon Special Burger: {title}", "diets": diets, "price": price}
        elif upper.startswith(("PANCHON BURGERIT", "LOUNASBURGERIT", "LOUNASSALAATTI", "LOUNASPIHVI", "PANCHON POKE")):
            # Lista toistuu sivulla jokaiselle päivälle; jokainen vakiolounas otetaan kerran
            key = " ".join(upper.split()[:2])
            if not any(" ".join(s.upper().split()[:2]) == key for s in static):
                text = re.sub(r"\s+", " ", line)
                text = re.sub(r"[–-]\s*[–-]", "–", text)  # "LOUNASPIHVI – – ..." -> "LOUNASPIHVI – ..."
                words = text.split(" ")
                # Isot kirjaimet otsikossa pieniksi ("PANCHON POKE" -> "Panchon poke")
                head = []
                while words and words[0].isalpha() and words[0].isupper() and len(words[0]) > 2:
                    head.append(words.pop(0).lower())
                line_text = " ".join(([" ".join(head).capitalize()] if head else []) + words).strip()
                line_text = re.sub(r"\(PG,\s*PL\)", "(gluteeniton ja laktoositon pyydettäessä)", line_text)
                line_text = re.sub(r"\(PG\)", "(gluteeniton pyydettäessä)", line_text)
                line_text = re.sub(r"\(PL\)", "(laktoositon pyydettäessä)", line_text)
                static.append(line_text)

    if special and has_any_items(menus):
        for idx in range(5):
            menus[str(idx)].append(dict(special))
    return menus, static or None


def parse_pancho_today(url: str, today: date) -> dict:
    """Pancho Villa, jonka sivulla on vain päivän lista ("Tänään 13.11.", esim. Tullintori).

    Jokainen lounas on oma lohkonsa: "MEKSIKOLOUNAS 13,70 €", vaihtoehdot/merkinnät ja kuvaus.
    Viikon aiemmat päivät täydentyvät aiemmista ajoista.
    """
    menus = empty_week()
    idx = today.weekday()
    if idx > 4:
        return menus
    html = fetch_html(url)
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    start = next((i for i, l in enumerate(lines) if re.match(r"^tänään\s+\d{1,2}\.\d{1,2}\.?$", l, re.IGNORECASE)), None)
    if start is None:
        print("  Panchon päivän listaa ei löytynyt")
        return menus
    dm = re.search(r"(\d{1,2})\.(\d{1,2})", lines[start])
    if (int(dm.group(1)), int(dm.group(2))) != (today.day, today.month):
        print(f"  Panchon lista on päivältä {lines[start]}, ohitetaan")
        return menus
    items = []
    for line in lines[start + 1:]:
        if line.lower() in ("palvelut", "aukioloajat", "yhteystiedot"):
            break
        head = re.match(r"^([A-ZÅÄÖ][A-ZÅÄÖ \-–&]{3,})\s+(\d{1,2}(?:[,.]\d{1,2})?\s*€.*)$", line)
        if head:
            pm = PRICE_RE.search(head.group(2))
            items.append({"title": head.group(1).strip().capitalize(), "diets": [], "price": format_price(pm) if pm else None,
                          "desc": "", "_extra": head.group(2)[pm.end():].strip(" /") if pm else ""})
            continue
        if not items:
            continue
        item = items[-1]
        diets = [d for m in PAREN_DIETS_RE.finditer(line) for d in re.split(r"[\s,]+", m.group(1))]
        item["diets"] = normalize_diets(item["diets"] + diets)
        text = PAREN_DIETS_RE.sub("", line).strip(" ,")
        if text:
            item["desc"] = f"{item['desc']} {text}".strip()
    for item in items:
        extra = item.pop("_extra")
        if extra:
            item["desc"] = f"{item['desc']} ({extra})".strip()
        if not item["desc"]:
            item.pop("desc")
    menus[str(idx)] = items
    return menus


# --------------------------------------------------------------------------
# Tampere: Tullintori
# --------------------------------------------------------------------------
def _slash_diets(text: str) -> str:
    """"L/G" ja "L,G" -> "L, G", jotta merkinnät tunnistetaan."""
    return re.sub(rf"\b({DIET_TOKEN})\s*/\s*(?={DIET_TOKEN}\b)", r"\1, ", text, flags=re.IGNORECASE)


def parse_brander(url: str, today: date) -> dict:
    """Brander-kahvila: "Maanantai 16.11" ja sen alla päivän keitot (merkinnät "M/G")."""
    menus = empty_week()
    html = fetch_html(url)
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    dates = week_dates(today)
    current = None
    for line in lines:
        m = re.match(r"^(maanantai|tiistai|keskiviikko|torstai|perjantai)\s+(\d{1,2})\.(\d{1,2})\.?$", line, re.IGNORECASE)
        if m:
            idx = WEEKDAYS_FI.index(m.group(1).lower())
            d = dates[idx]
            current = idx if (int(m.group(2)), int(m.group(3))) == (d.day, d.month) else None
            continue
        if re.match(r"^(lauantai|sunnuntai|konditoriatuotteet|kakut|suolaiset)\b", line, re.IGNORECASE):
            current = None
            continue
        if current is None:
            continue
        title, diets, price = parse_dish_line(_slash_diets(line))
        if title and len(title) > 3:
            menus[str(current)].append({"title": title, "diets": diets, "price": price})
    return menus


def parse_pyymaen(slug: str, today: date) -> dict:
    """Pyymäen kahvila: viikon salaatit ja uuniperuna joka päivä sekä päivän keitto.

    Sivun osoite on viikkokohtainen: /kahvilat/lounaslista/<slug>/<viikko>/
    """
    menus = empty_week()
    week = today.isocalendar()[1]
    html = fetch_html(f"https://www.pyymaen.fi/kahvilat/lounaslista/{slug}/{week}/")
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    start = next((i for i, l in enumerate(lines) if re.search(rf"viikko\s+{week}\b", l, re.IGNORECASE)), None)
    if start is None:
        print(f"  Pyymäen viikon {week} listaa ei löytynyt")
        return menus
    salads, others, soups = [], [], {}
    for line in lines[start + 1:]:
        if line.startswith("«") or line.lower().startswith(("lataa pdf", "tervetuloa")):
            break
        day = re.match(r"^(maanantai|tiistai|keskiviikko|torstai|perjantai)\s*:\s*(.+)$", line, re.IGNORECASE)
        if day:
            idx = WEEKDAYS_FI.index(day.group(1).lower())
            text = day.group(2).strip()
            if re.search(r"kiinni|suljettu|ei lounasta", text, re.IGNORECASE):
                soups[idx] = None
                continue
            title, diets, price = parse_dish_line(_slash_diets(text))
            soups[idx] = {"title": f"Päivän keitto: {title}", "diets": diets, "price": price}
            continue
        if re.match(r"^vaihtuvat keitot", line, re.IGNORECASE) or not PRICE_RE.search(line):
            continue
        title, diets, price = parse_dish_line(_slash_diets(line))
        if not title:
            continue
        (salads if re.search(r"salaatti$", title, re.IGNORECASE) else others).append(
            {"title": title, "diets": diets, "price": price})
    common = []
    if salads:
        common.append(package("Lounassalaatit", [{"title": s["title"], "diets": s["diets"]} for s in salads], salads[0]["price"]))
    common += others
    for idx in range(5):
        if idx in soups and soups[idx] is None:
            continue  # päivä suljettu
        items = ([soups[idx]] if soups.get(idx) else []) + [dict(i) for i in common]
        menus[str(idx)] = items
    return menus


def parse_lospollos(today: date) -> dict:
    """Los Pollos Grill & Bar (pollos.fi): sivuston oma JSON-rajapinta."""
    menus = empty_week()
    data = fetch_json("https://pollos.fi/api/public/lunch")
    if not data:
        return menus
    dates = [d.isoformat() for d in week_dates(today)]
    for key in ("mon", "tue", "wed", "thu", "fri"):
        day = data.get(key) or {}
        if day.get("date") not in dates or day.get("closed") or day.get("nolunch"):
            continue
        items = []
        for it in sorted(day.get("lunchItems") or [], key=lambda i: i.get("lunchItemVisibilityOrder", 0)):
            if it.get("hidden"):
                continue
            title = re.sub(r"[^\w\s&,.'’()/-]", "", it.get("lunchItemTitle") or "").strip()
            if not title:
                continue
            diets = []
            if it.get("glutenFree"):
                diets.append("G")
            elif it.get("glutenFreeOnRequest"):
                diets.append("PG")
            if it.get("lactoseFree"):
                diets.append("L")
            if it.get("vegan"):
                diets.append("Veg")
            price = it.get("lunchItemPrice")
            desc = re.sub(r"\s+", " ", it.get("lunchItemDescription") or "").strip()
            item = {
                "title": title.capitalize() if title.isupper() else title,
                "diets": [d for d in DIET_ORDER if d in diets],
                "price": f"{price:.2f}".replace(".", ",") + " €" if isinstance(price, (int, float)) else None,
            }
            if desc:
                item["desc"] = desc.capitalize() if desc.isupper() else desc
            items.append(item)
        menus[str(dates.index(day["date"]))] = items
    return menus


# --------------------------------------------------------------------------
# ==========================================================================
# TAMPERE (Ratina)
# ==========================================================================
def split_diet_groups(text: str):
    """"Rapeaa seitiä (M, G), tartarkastiketta (L, G) ja perunamuusia (L, G)"
    -> ("Rapeaa seitiä, tartarkastiketta ja perunamuusia", ["G"])

    Kun rivillä on useita merkintäryhmiä, koko annokselle annetaan vain merkinnät,
    jotka ovat jokaisessa ryhmässä (esim. G yllä). Sulkeissa oleva "(G)" yhden
    merkinnän sisällä tarkoittaa "pyydettäessä".
    """
    groups = []

    def grab(m):
        tokens = [t for t in re.split(r"[\s,]+", m.group(1)) if t]
        groups.append(normalize_diets(tokens))
        return ""

    title = re.sub(rf"\(\s*({DIET_TOKEN}(?:[\s,]+{DIET_TOKEN})*)\s*\)", grab, text, flags=re.IGNORECASE)
    # Alkuperämerkinnät, esim. "(suomalaista nautaa ja possua)", pois otsikosta
    title = re.sub(r"\(\s*suomalaista[^)]*\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+([,.])", r"\1", title)
    title = re.sub(r",\s*(ja|&)\s", r" \1 ", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" ,-–")
    diets = sorted(set.intersection(*(set(g) for g in groups)), key=DIET_ORDER.index) if groups else []
    return title, diets


def package(title: str, parts: list, price=None) -> dict:
    """Annoskokonaisuus (buffet tms.), jonka osat näytetään omine merkintöineen."""
    common = set.intersection(*(set(p["diets"]) for p in parts)) if parts else set()
    return {
        "title": title,
        "diets": [d for d in DIET_ORDER if d in common],
        "price": price,
        "parts": parts,
    }


def parse_jamix(kitchen_id: int, today: date, customer: str = "93077") -> dict:
    """Juvenes-ravintolat (Jamix-ruokalistapalvelu)."""
    menus = empty_week()
    data = fetch_json(
        f"https://fi.jamix.cloud/apps/menuservice/rest/haku/menu/{customer}/{kitchen_id}?lang=fi&type=json"
    )
    if not data:
        return menus
    dates = {d.strftime("%Y%m%d"): i for i, d in enumerate(week_dates(today))}
    for kitchen in data:
        for menu_type in kitchen.get("menuTypes", []):
            for menu in menu_type.get("menus", []):
                for day in menu.get("days", []):
                    idx = dates.get(str(day.get("date")))
                    if idx is None:
                        continue
                    for option in day.get("mealoptions", []):
                        parts = []
                        for item in option.get("menuItems", []):
                            name = re.sub(r"\s+", " ", item.get("name") or "").strip()
                            if not name:
                                continue
                            diets = normalize_diets(re.split(r"[,\s]+", item.get("diets") or ""))
                            parts.append({"title": name, "diets": diets})
                        if parts:
                            title = (option.get("name") or "Lounas").strip()
                            menus[str(idx)].append(package(title[:1].upper() + title[1:].lower(), parts))
    return menus


def parse_huili(today: date) -> dict:
    """Neste Hatanpää / Huili: viikon lista PDF:nä, päivän lista sivulla."""
    menus = empty_week()
    html = fetch_html("https://huilipiste.fi/lounas/")
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")
    price = "13,90 €"
    pm = re.search(r"Lounas[^.]{0,60}?(\d{1,2}[.,]\d{2})\s*€", soup.get_text(" "))
    if pm:
        price = format_price(PRICE_RE.search(pm.group(1) + " €"))

    def dish(text):
        title, diets = split_diet_groups(text)
        return {"title": title, "diets": diets} if title else None

    # Viikon lista PDF:stä
    pdf_link = next((a["href"] for a in soup.find_all("a", href=True) if a["href"].lower().endswith(".pdf")), None)
    if pdf_link:
        content = fetch_bytes(pdf_link)
        if content:
            try:
                from pypdf import PdfReader

                text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
                day_names = [d.upper() for d in WEEKDAYS_FI] + ["LAUANTAI", "SUNNUNTAI"]
                current = None
                buffer = []

                def flush():
                    if current is not None and current < 5 and buffer:
                        parts = [x for x in (dish(b) for b in buffer) if x]
                        if parts:
                            menus[str(current)] = [package("Lounasbuffet", parts, price)]

                for raw in text.splitlines():
                    line = raw.strip()
                    if line.upper() in day_names:
                        flush()
                        current, buffer = day_names.index(line.upper()), []
                        continue
                    if current is None or not line:
                        continue
                    if line.startswith("•"):
                        buffer.append(line.lstrip("• ").strip())
                    elif buffer and not re.match(r"^(hintaan|lounas|kaikki|l = )", line, re.IGNORECASE):
                        buffer[-1] += " " + line
                flush()
            except Exception as e:
                print(f"  Huilin PDF:n luku epäonnistui: {e}")

    # Päivän lista sivulta on aina ajantasaisin -> korvaa tämän päivän
    if today.weekday() < 5:
        lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
        # Sivun päivämäärä (esim. "perjantai 16.10.") on oltava tämä päivä
        i_today = lines.index("Päivän lounas") if "Päivän lounas" in lines else None
        date_ok = i_today is not None and any(
            f"{today.day}.{today.month}." in l for l in lines[max(0, i_today - 2): i_today]
        )
        if i_today is not None and not date_ok:
            print("  Huilin päivän lista ei ole tältä päivältä, käytetään PDF:ää")
        if date_ok:
            i = i_today + 1
            today_parts = []
            for line in lines[i:]:
                if line.lower().startswith("katso koko viikon"):
                    break
                if PRICE_RE.search(line) or re.match(r"^\d{1,2}[.,]\d{2}\s*€$", line):
                    continue
                # Sivulla merkinnät ilman sulkeita: "Herkkusienikeitto L, G"
                line = re.sub(rf"((?:\s|,)+{DIET_TOKEN}(?:\s*,\s*{DIET_TOKEN})*)(?=,|\s+ja\s|$)",
                              lambda m: f" ({m.group(1).strip(' ,')})", line, flags=re.IGNORECASE)
                d = dish(line)
                if d:
                    today_parts.append(d)
            if today_parts:
                menus[str(today.weekday())] = [package("Lounasbuffet", today_parts, price)]
    return menus


def parse_momento() -> dict:
    """Momento Ratina: viikkotarjoukset (ma-pe), samat koko viikon."""
    menus = empty_week()
    html = fetch_html("https://ristorantemomento.fi/momento/ratina/")
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")
    anchor = soup.find(string=re.compile(r"Viikkotarjoukset", re.IGNORECASE))
    if not anchor:
        return menus
    items = []
    for h3 in anchor.find_all_next("h3"):
        price_el = h3.select_one(".hinta")
        if not price_el:
            if items:
                break
            continue
        pm = re.search(r"(\d{1,2})[.,](\d{2})", price_el.get_text())
        price = f"{pm.group(1)},{pm.group(2)} €" if pm else None
        price_el.decompose()
        desc_el = h3.find_next_sibling("p")
        desc = desc_el.get_text(" ", strip=True).lstrip("-– ").strip() if desc_el and "kuvaus" in (desc_el.get("class") or []) else None
        title, diets, _ = parse_dish_line(h3.get_text(" ", strip=True))
        if desc:
            desc = desc[:1].upper() + desc[1:]
        items.append({"title": title, "diets": diets, "price": price, **({"desc": desc} if desc else {})})
    for d in range(5):
        menus[str(d)] = [dict(i) for i in items]
    return menus


def parse_johnscotts(today: date):
    """John Scott's Ratina: viikon lounaslista. Palauttaa (menus, staticNote)."""
    menus = empty_week()
    html = fetch_html("https://johnscotts.se/ratina/john-scotts-lunch/")
    if not html:
        return menus, None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    week_i = next((i for i, l in enumerate(lines) if re.match(r"^Viikko\s+\d+$", l)), None)
    if week_i is None:
        return menus, None
    if int(lines[week_i].split()[-1]) != today.isocalendar()[1]:
        # Ravintola päivittää välillä listan mutta unohtaa otsikon viikkonumeron.
        # Hyväksytään lista, jos sivua on muokattu aikaisintaan kuluvaa maanantaita
        # edeltävänä lauantaina (WordPressin article:modified_time).
        modified = None
        meta = soup.find("meta", attrs={"property": "article:modified_time"})
        if meta and meta.get("content"):
            try:
                modified = datetime.fromisoformat(meta["content"]).astimezone(ZoneInfo("Europe/Helsinki")).date()
            except ValueError:
                modified = None
        week_start = week_dates(today)[0] - timedelta(days=2)
        if modified is None or modified < week_start:
            print(f"  John Scott'sin lista on viikolta {lines[week_i]} (muokattu {modified}), ohitetaan")
            return menus, None
        print(f"  John Scott'sin otsikossa {lines[week_i]}, mutta sivua muokattu {modified}: käytetään listaa")

    price, current, static = None, None, []
    for line in lines[week_i + 1:]:
        if price is None and re.match(r"^\d{1,2}[.,]\d{2}\s*€$", line):
            price = format_price(PRICE_RE.search(line))
            continue
        day_idx = match_weekday(line)
        if day_idx is not None and line.lower().strip(":") in WEEKDAYS_FI:
            current = day_idx
            continue
        if re.match(r"^viikon leike", line, re.IGNORECASE):
            current = "static"
            continue
        if line.startswith("John Scott's Ratina") or line.startswith("John Scott’s Ratina"):
            break
        if current == "static":
            if PRICE_RE.search(line) or not static:
                static.append(line)
            else:
                static[-1] += f" – {line}"
        elif current is not None:
            title, diets = split_diet_groups(line)
            if title and not title.endswith(":"):
                menus[str(current)].append({"title": title, "diets": diets})
    for d in range(5):
        if menus[str(d)]:
            menus[str(d)] = [package("Päivän lounas", menus[str(d)], price)]
    return menus, static or None


def parse_via_tribunali(today: date):
    """Via Tribunali: viikon lounasmenu (sama koko viikon). Palauttaa (menus, info)."""
    menus = empty_week()
    html = fetch_html("https://viatribunali.fi/lounasmenu/")
    if not html:
        return menus, []
    soup = BeautifulSoup(html, "html.parser")
    week_h = soup.find(lambda t: t.name in ("h1", "h2", "h3") and re.match(r"^\s*Viikko\s+\d+", t.get_text()))
    if not week_h:
        return menus, []
    if int(re.search(r"\d+", week_h.get_text()).group()) != today.isocalendar()[1]:
        print(f"  Via Tribunalin lista on {week_h.get_text(strip=True)}, ohitetaan")
        return menus, []

    items = []
    for h4 in week_h.find_all_next("h4"):
        box = h4.find_parent("div", class_="col-lg-8") or h4.parent
        name = h4.get_text(" ", strip=True)
        price_el = box.select_one("p.sdm-3xl--text")
        pm = re.search(r"\d{1,2}(?:[.,]\d{2})?", price_el.get_text()) if price_el else None
        price = format_price(PRICE_RE.search(pm.group() + " €")) if pm else None
        sub_el = box.select_one("p.sdm-xl--text")
        diet_els = box.select("p.sdm--uppercase")
        diet_text = diet_els[-1].get_text(" ", strip=True) if diet_els else ""
        # "L (G)" = laktoositon, gluteeniton pyydettäessä; "Vegaaninen" = vegaaninen
        base = re.sub(r"\(.*?\)", "", diet_text)
        optional = " ".join(re.findall(r"\((.*?)\)", diet_text))
        diets = normalize_diets(re.split(r"[\s,]+", base))
        if re.search(r"\bG\b", optional):
            diets = normalize_diets(diets + ["PG"])
        skip = {id(e) for e in [price_el, sub_el, *diet_els] if e}
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for p in box.find_all("p")
            if id(p) not in skip
            for line in p.get_text("\n").splitlines()
            if line.strip()
        ]
        # Rivit yhteen; isolla alkavan uuden lauseen eteen piste, jos edellinen ei pääty välimerkkiin
        desc = ""
        for line in lines:
            if desc and line[:1].isupper() and not re.search(r"[.!?:]$", desc):
                desc += "."
            desc = f"{desc} {line}".strip()
        title = f"{name} ({sub_el.get_text(strip=True).lower()})" if sub_el else name
        items.append({"title": title, "diets": diets, "price": price, **({"desc": desc} if desc else {})})
    info = []
    if soup.find(string=re.compile(r"Gluteeniton pizzataikina", re.IGNORECASE)):
        info.append("Gluteeniton pizzataikina +4 € (keittiö ei ole gluteeniton ympäristö).")
    for d in range(5):
        menus[str(d)] = [dict(i) for i in items]
    return menus, info


# Yhteislistan rivillä voi olla toimipistekohtainen vaihtoehto:
# "Makkaramix (G,L) Jyvässeudun toimipisteissä Uunimakkaraa (G,L)"
CAFFITELLA_LOCAL_RE = re.compile(
    r"\s*\b([A-ZÅÄÖ][\wåäö-]*(?:\s+(?:ja|sekä)\s+[\wåäö-]+)*)\s+toimipiste\w*\s*:?\s*(.*)$"
)
CAFFITELLA_HERE_RE = re.compile(r"tampere|ratina|pirkanmaa", re.IGNORECASE)


def caffitella_local_variant(line: str) -> str:
    """Valitsee Ratinaa koskevan osan rivistä, jolla on toimipistekohtainen vaihtoehto."""
    m = CAFFITELLA_LOCAL_RE.search(line)
    if not m:
        return line
    if CAFFITELLA_HERE_RE.search(m.group(1)):
        return m.group(2).strip()
    return line[: m.start()].strip()


def parse_caffitella(today: date) -> dict:
    """Caffitella Ratina: viikon lounasbuffet (Ratinan lista omana osionaan)."""
    menus = empty_week()
    html = fetch_html("https://www.caffitella.fi/lounaslista/")
    if not html:
        return menus
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    starts = [i for i, l in enumerate(lines) if l.lower().startswith("tämä lounaslista tarjolla")]
    block = None
    for n, i in enumerate(starts):
        if i + 1 < len(lines) and "ratina" in lines[i + 1].lower():
            block = lines[i + 2: starts[n + 1] if n + 1 < len(starts) else len(lines)]
            break
    if not block:
        print("  Caffitellan Ratinan listaa ei löytynyt")
        return menus

    week_m = next((re.search(r"(\d+)", l) for l in block if l.lower().startswith("viikkonumero")), None)
    if week_m and int(week_m.group(1)) != today.isocalendar()[1]:
        print(f"  Caffitellan lista on viikolta {week_m.group(1)}, ohitetaan")
        return menus
    price_line = next((l for l in block if l.lower().startswith("lounasbuffet")), "")
    pm = LOOSE_PRICE_RE.search(price_line)
    price = format_price(pm) if pm else None

    day_names = [d.upper() for d in WEEKDAYS_FI]
    current, buffet, alacarte, after_salad = None, [], [], False

    def flush():
        if current is None:
            return
        items = []
        if buffet:
            items.append(package("Lounasbuffet", buffet, price))
        if alacarte:
            items.append(package("Annosruoka (ei sisälly buffettiin)", alacarte))
        menus[str(current)] = items

    for line in block:
        if line.upper() in day_names:
            flush()
            current, buffet, alacarte, after_salad = day_names.index(line.upper()), [], [], False
            continue
        if line.upper() == "LAUANTAI" or line.startswith("G = "):
            break
        if current is None:
            continue
        # Lähteessä edellisen rivin merkintä voi alkaa seuraavalta riviltä: "(L) Burger salaatti (G,L)"
        lead = re.match(rf"^(\(\s*{DIET_TOKEN}(?:[\s,]+{DIET_TOKEN})*\s*\))\s*(.*)$", line, re.IGNORECASE)
        target = alacarte if after_salad else buffet
        if lead and target:
            title, diets = split_diet_groups(f"{target[-1]['title']} {lead.group(1)}")
            target[-1] = {"title": title, "diets": diets}
            line = lead.group(2)
            if not line:
                continue
        line = re.sub(r"\(\s*annosruoka ei sis\.? buffettiin\s*\)", "", line, flags=re.IGNORECASE).strip()
        line = caffitella_local_variant(line)
        if not line:
            continue
        if line.lower() == "salaattibuffet":
            buffet.append({"title": "Salaattibuffet", "diets": []})
            after_salad = True
            continue
        # Annosruokarivillä voi olla useampi annos: "Broiler burger (L) Tofu poke (G,VE)"
        pieces = (
            re.split(r"(?<=\))\s+(?:ja\s+|&\s+)?(?=[A-ZÅÄÖ])", line) if after_salad else [line]
        )
        for piece in pieces:
            title, diets = split_diet_groups(piece)
            if title:
                (alacarte if after_salad else buffet).append({"title": title, "diets": diets})
    flush()
    return menus


# Merkkejä jäsennysvirheestä: toimipistekohtainen teksti, arvosanat, navigaatio tai hinta otsikossa
SUSPICIOUS_RES = [
    (re.compile(r"toimipiste", re.IGNORECASE), "toimipisteteksti"),
    (re.compile(r"\d[,.]\d\s*/\s*5"), "arvosana"),
    (re.compile(r"\b(etusivu|valikko|evästeet|lue lisää)\b", re.IGNORECASE), "navigaatioteksti"),
    # "extra 0,25e" ja "+1,00 €" ovat lisähintoja, eivät otsikkoon jäänyt annoksen hinta
    (re.compile(r"(?<!extra )(?<!\+)(?<!\+ )\b\d+[,.]\d{2}\s*(€|e\b|eur)", re.IGNORECASE), "hinta otsikossa"),
]


def quality_warnings(menus: dict) -> list:
    """Palauttaa listan epäilyttävistä annosriveistä, esim. "pe: toimipisteteksti: Makkaramix …"."""
    warnings = []
    for key in sorted(menus):
        for item in menus[key]:
            for entry in [item, *item.get("parts", [])]:
                title = entry.get("title", "")
                reasons = [name for rx, name in SUSPICIOUS_RES if rx.search(title)]
                if len(title) > 120:
                    reasons.append("pitkä otsikko")
                if reasons:
                    day = WEEKDAYS_FI[int(key)][:2].lower()
                    warnings.append(f"{day}: {', '.join(reasons)}: {title[:80]}")
    return warnings


