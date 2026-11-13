// Palvelee kaikkia toimipisteiden lounassivuja samasta koodista.
//
// Toimipiste valitaan osoitteen perusteella (sites.json):
//   - aladomain, esim. pinninkatu.aihio.fyi  -> hosts-lista
//   - polku, esim. ruokis.aihio.fyi/pinninkatu
// Muuten näytetään oletustoimipiste (defaultSite).
//
// Somepalvelut (WhatsApp, Facebook, Slack ym.) lukevat linkin esikatselun HTML:stä ilman
// JavaScriptiä, joten toimipisteen otsikko, kuvaus ja kuva kirjoitetaan sivuun jo täällä.
// Samalla <html>-elementti saa data-site- ja data-theme-attribuutit, jolloin oikea teema
// näkyy heti ilman välähdystä.
import conf from "./sites.json";

const published = (id) => Boolean(conf.sites[id]) && conf.sites[id].published !== false;

function siteFor(url) {
  for (const [id, site] of Object.entries(conf.sites)) {
    if (published(id) && (site.hosts || []).includes(url.hostname)) return id;
  }
  const m = url.pathname.match(/^\/([a-z0-9-]+)\/?$/);
  if (m && published(m[1])) return m[1];
  return null;
}

const abs = (origin, p) => (/^https?:\/\//.test(p) ? p : origin + "/" + p.replace(/^\//, ""));

class SetAttr {
  constructor(attrs) {
    this.attrs = attrs;
  }
  element(el) {
    for (const [k, v] of Object.entries(this.attrs)) el.setAttribute(k, v);
  }
}

// Huom: kentän nimi ei saa olla "text", koska HTMLRewriter tulkitsee sen tekstinkäsittelijäksi
class SetText {
  constructor(value) {
    this.value = value;
  }
  element(el) {
    el.setInnerContent(this.value);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const isPage = url.pathname === "/" || url.pathname === "/index.html" || /^\/[a-z0-9-]+\/?$/.test(url.pathname);
    if (!isPage) return env.ASSETS.fetch(request);

    const id = siteFor(url);
    // Tuntematon polku, joka ei ole toimipiste: tavallinen tiedosto tai 404
    if (!id && url.pathname !== "/" && url.pathname !== "/index.html") return env.ASSETS.fetch(request);

    const siteId = id || conf.defaultSite;
    const site = conf.sites[siteId];
    // Assets-palvelu ohjaa /index.html-osoitteen juureen, joten haetaan sivu juuresta
    const page = await env.ASSETS.fetch(new Request(new URL("/", url), request));
    const origin = url.origin;

    return new HTMLRewriter()
      .on("html", new SetAttr({ "data-site": siteId, "data-theme": site.theme || "" }))
      .on("title", new SetText(site.title))
      .on("#metaDescription", new SetAttr({ content: site.description }))
      .on("#ogTitle", new SetAttr({ content: site.title }))
      .on("#ogDescription", new SetAttr({ content: site.description }))
      .on("#ogImage", new SetAttr({ content: abs(origin, site.ogImage) }))
      .on("#themeColor", new SetAttr({ content: site.themeColor }))
      .on("#faviconSvg", new SetAttr({ href: "/" + site.icons.svg }))
      .on("#faviconPng", new SetAttr({ href: "/" + site.icons.png }))
      .on("#touchIcon", new SetAttr({ href: "/" + site.icons.touch }))
      .transform(page);
  },
};
