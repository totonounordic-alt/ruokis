"""Hakee toimipisteiden lounaslistat ja kirjoittaa ne tiedostoihin data/<toimipiste>.json.

Toimipisteet ovat tiedostossa sites.json ja ravintolat rekisterissä restaurants.json.
Jokainen ravintola haetaan kerran, vaikka se näkyisi usean toimipisteen sivulla.

Käyttö: python scraper/update.py            (kaikki toimipisteet)
        python scraper/update.py pinninkatu (vain yksi)
"""
import json
import math
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import parsers as p

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCE_LABELS = {
    "lounaat_info": "lounaat.info",
    "compass": "Compass Group",
    "jamix": "Jamix",
    "sodexo": "Sodexo",
    "static": "lounaat.info",
}


def fetch_source(source: dict, today) -> dict:
    """Hakee yhden ravintolan viikon listan lähteen tyypin mukaan."""
    kind = source["type"]
    if kind == "lounaat_info":
        menus, info = p.parse_lounaat_info(source["url"], today, source.get("package_title"))
        return {"menus": menus, "info": info}
    if kind == "compass":
        return {"menus": p.parse_compass(source["url"], today)}
    if kind == "static":
        # Kiinteä viikkotarjous (esim. buffet, jonka päivän ruokia ei julkaista)
        return {"menus": {str(d): [dict(i) for i in source["items"]] for d in range(5)}}
    if kind == "sodexo":
        return {"menus": p.parse_sodexo(source["id"], today)}
    if kind == "jamix":
        return {"menus": p.parse_jamix(source["kitchen"], today, source.get("customer", "93077"))}
    raise ValueError(f"Tuntematon lähdetyyppi: {kind}")


def distance_m(a: dict, b: dict) -> int:
    """Linnuntie metreinä (riittää lähiravintoloiden järjestämiseen)."""
    x = math.radians(b["lon"] - a["lon"]) * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    y = math.radians(b["lat"] - a["lat"])
    return round(6371000 * math.hypot(x, y))


def previous_menus(path: str, week_key: str) -> dict:
    """Saman viikon aiemman ajon listat, joilla paikataan päivät, joita ei nyt saatu."""
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return {}
    if old.get("week") != week_key:
        return {}
    return {r["id"]: r["menus"] for r in old.get("restaurants", []) if p.has_any_items(r.get("menus", {}))}


def fill_week(new: dict, prev: dict) -> dict:
    result = {}
    for d in range(5):
        key = str(d)
        items = new.get(key) or []
        if not items:
            prev_items = prev.get(key) or []
            if prev_items and prev_items != [p.PLACEHOLDER_ITEM]:
                items = prev_items
            else:
                items = [dict(p.PLACEHOLDER_ITEM)]
        result[key] = items
    return result


def main():
    with open(os.path.join(ROOT, "sites.json"), encoding="utf-8") as f:
        sites = json.load(f)["sites"]
    with open(os.path.join(ROOT, "restaurants.json"), encoding="utf-8") as f:
        registry = json.load(f)["restaurants"]

    selected = sys.argv[1:] or list(sites)
    now_fi = datetime.now(ZoneInfo("Europe/Helsinki"))
    today = now_fi.date()
    iso_year, iso_week, _ = today.isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"

    # Haetaan jokainen tarvittava ravintola kerran
    fetched = {}
    for rid in dict.fromkeys(r for s in selected for r in sites[s]["restaurants"]):
        spec = registry[rid]
        print(f"Haetaan {spec['name']}...")
        try:
            fetched[rid] = fetch_source(spec["source"], today) or {}
        except Exception as e:
            print(f"  VIRHE ({rid}): {e}")
            fetched[rid] = {}
        found = sum(1 for d in range(5) if (fetched[rid].get("menus") or {}).get(str(d)))
        print(f"  {rid}: {found}/5 päivää haettu")

    for site_id in selected:
        site = sites[site_id]
        path = os.path.join(ROOT, site["data"])
        prev = previous_menus(path, week_key)
        out = []
        for rid in site["restaurants"]:
            spec = registry[rid]
            result = dict(fetched.get(rid) or {})
            menus = result.pop("menus", None) or p.empty_week()
            source = spec["source"]
            rest = {
                "id": rid,
                "name": spec["name"],
                "address": spec["address"],
                "hours": spec.get("hours"),
                "price": spec.get("price"),
                "lat": spec["lat"],
                "lon": spec["lon"],
                "distance": distance_m(site["center"], spec),
                "url": spec.get("url"),
                "source": {"label": SOURCE_LABELS.get(source["type"], source["type"]), "url": source.get("url")},
            }
            for key in ("info", "staticNote"):
                if spec.get(key) or result.get(key):
                    rest[key] = result.get(key) or spec.get(key)
            rest["menus"] = fill_week(menus, prev.get(rid, {}))
            # Aamutarkistus raportoi nämä; sivusto ei näytä niitä
            warnings = p.quality_warnings(rest["menus"])
            if warnings:
                rest["warnings"] = warnings
                for w in warnings:
                    print(f"  VAROITUS ({rid}): {w}")
            out.append(rest)
        out.sort(key=lambda r: r["distance"])
        data = {
            "site": site_id,
            "updated": now_fi.strftime("%d.%m.%Y klo %H.%M"),
            "week": week_key,
            "restaurants": out,
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Valmis! {site['data']} päivitetty ({data['updated']}).")


if __name__ == "__main__":
    main()
