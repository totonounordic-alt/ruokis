# Lounassivut: yhteinen pohja kaikille toimipisteille

Yksi koodipohja usean toimipisteen lounassivuille. Ensimmäisenä julkaistaan
**Pinninkadun lounas** (Tampere): https://pinninkatu.aihio.fyi

## Rakenne

| Tiedosto | Mitä |
|---|---|
| `sites.json` | Toimipisteet: nimi, sijainti, teema, kuvat, palauteprojekti ja ravintolalista. Hakuskripti, sivu ja Worker lukevat tämän. |
| `restaurants.json` | Ravintolarekisteri: kukin ravintola kerran, myös lähde (lounaat.info, Compass, Sodexo, Jamix…). |
| `scraper/parsers.py` | Lähteiden jäsentimet (siirretty kokkola-lounas-reposta). |
| `scraper/update.py` | Hakee kunkin ravintolan kerran ja kirjoittaa tiedostot `data/<toimipiste>.json`. |
| `index.html` | Sivu. Lukee toimipisteen asetukset `sites.json`-tiedostosta. |
| `worker.js` | Cloudflare Worker: valitsee toimipisteen osoitteesta ja kirjoittaa linkin esikatselun tiedot sivuun. |
| `.github/workflows/update-menus.yml` | Päivitysajot arkiaamuisin. |

## Osoitteet

Toimipiste valitaan osoitteen perusteella, ja molemmat tavat toimivat yhtä aikaa:

- **aladomain**, esim. `pinninkatu.aihio.fyi` (`hosts` sites.jsonissa)
- **polku**, esim. `<yhteinen-osoite>/pinninkatu`

Muuten näytetään oletustoimipiste (`defaultSite`). Paikallisesti voi käyttää myös `?paikka=pinninkatu`.

## Toimipisteen lisääminen

1. Lisää ravintolat `restaurants.json`-tiedostoon (koordinaatit esim. OpenStreetMapista).
2. Lisää toimipiste `sites.json`-tiedostoon. Kun `published` on `false`, toimipiste ei näy vielä missään.
3. Lisää teema (`:root[data-theme="…"]`) ja kuvat `assets/`-kansioon.
4. Lisää aladomain Cloudflaressa Workerin Custom domains -asetuksiin.

## Lähdetyypit (restaurants.json → source.type)

| Tyyppi | Kentät |
|---|---|
| `lounaat_info` | `url`, valinnainen `package_title` |
| `compass` | `url` (Compass Groupin ravintolasivu) |
| `sodexo` | `id` (Sodexon weekly_json-rajapinnan tunniste), `url` |
| `jamix` | `kitchen`, valinnainen `customer` |
| `static` | `items` (kiinteä viikkotarjous), `url` |

Hakuskripti merkitsee epäilyttävät rivit (esim. hinta otsikossa, toimipisteteksti) kenttään
`warnings`. Sivu ei näytä niitä, mutta aamutarkistus raportoi ne.

## Paikallinen testaus

```
pip install requests beautifulsoup4 pypdf
python scraper/update.py            # kaikki toimipisteet
python scraper/update.py pinninkatu # yksi
npx wrangler dev                    # sivu ja Worker osoitteessa http://localhost:8787/pinninkatu
```
