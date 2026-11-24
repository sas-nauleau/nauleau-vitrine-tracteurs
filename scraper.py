#!/usr/bin/env python3
"""
Récupère automatiquement les annonces (tracteurs ET matériels) de
nauleau-agricole.com, puis :
  1. les envoie dans la base Supabase du projet "vitrine-nauleau" (c'est elle
     que lit l'écran TV et que pilote la page d'administration) ;
  2. garde une copie dans data/annonces.json (secours si la base est
     injoignable) et data/tracteurs.json (ancien format, tracteurs seuls).

Le script repère les annonces via leur URL (qui contient toujours un numéro
d'annonce, ex: /tracteurs-materiels/tracteurs/10624-fendt-311-profi.html)
plutôt que via des classes CSS qui peuvent changer. Les catégories de
matériel sont découvertes automatiquement à partir des liens du site.

Mode "léger" (par défaut) : pour ménager le site, le robot ne lit que les pages
de liste (environ 25 pages), n'ouvre en détail que les NOUVELLES annonces et
rafraîchit chaque jour 1/7 des annonces déjà connues (chacune est donc relue
une fois par semaine). Les autres sont reprises de data/annonces.json.
Mode "complet" (FULL_REFRESH=1) : ouvre toutes les fiches, comme avant.
Au moindre refus ou à la moindre erreur du site, le robot s'arrête sans rien
modifier : il n'insiste jamais.

Variables d'environnement (renseignées par le workflow GitHub) :
  SUPABASE_URL          adresse du projet Supabase
  SUPABASE_SERVICE_KEY  clé secrète permettant d'écrire dans la base
  FULL_REFRESH          (facultatif) "1" ou "true" : relit toutes les fiches
  EXTRA_LISTING_PATHS   (facultatif) pages de liste supplémentaires à lire,
                        séparées par des virgules, ex: /tracteurs-materiels/chargeurs/
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.nauleau-agricole.com"
HUB_PATH = "/tracteurs-materiels/"
TRACTEURS_PATH = "/tracteurs-materiels/tracteurs/"
MAX_PAGES = 30          # garde-fou : pages de liste lues par catégorie
MAX_ROOTS = 40          # garde-fou : nombre de pages de liste explorées
MAX_DEPTH = 3           # garde-fou : profondeur d'exploration des sous-catégories
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NauleauVitrineBot/1.0; "
                  "+https://github.com/) "
}

# Une annonce : /tracteurs-materiels/<catégorie>/[<sous-catégorie>/]123456-nom.html
PRODUCT_URL_RE = re.compile(r"/tracteurs-materiels/((?:[a-z0-9-]+/)*)(\d+)-[^/]+\.html$")
# Une page de liste (catégorie ou sous-catégorie) : toujours terminée par "/"
DIR_RE = re.compile(r"^/tracteurs-materiels/((?:[a-z0-9-]+/)+)$")

PRICE_RE = re.compile(r"(\d{1,3}(?:[\s ]\d{3})*,\d{2})\s*€")

OUTPUT_ALL = "data/annonces.json"
OUTPUT_TRACTORS = "data/tracteurs.json"
OUTPUT_REPORT = "data/rapport-robot.json"

LIST_DELAY = 2.0        # secondes entre deux pages de liste
DETAIL_DELAY = 2.0      # secondes entre deux fiches
MAX_CONSECUTIVE_ERRORS = 3   # au-delà, le robot cesse d'interroger le site
REFRESH_CYCLE = 7       # chaque annonce connue est relue une fois tous les 7 jours


class SiteUnavailable(Exception):
    """Le site refuse ou ne répond pas : on arrête tout sans rien modifier."""


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def page_url(root_path, page_number):
    if page_number == 1:
        return urljoin(BASE_URL, root_path)
    return urljoin(BASE_URL, f"{root_path}page-{page_number}.html")


def extract_links(url, html):
    """Repère, sur une page de liste, les annonces et les pages de catégories."""
    soup = BeautifulSoup(html, "html.parser")
    products, dirs = [], []
    seen_p, seen_d = set(), set()
    for a in soup.find_all("a", href=True):
        full_url = urljoin(url, a["href"]).split("#")[0].split("?")[0]
        parsed = urlparse(full_url)
        if parsed.netloc and not parsed.netloc.endswith("nauleau-agricole.com"):
            continue
        if PRODUCT_URL_RE.search(parsed.path):
            if full_url not in seen_p:
                seen_p.add(full_url)
                products.append(full_url)
        elif DIR_RE.match(parsed.path):
            if parsed.path not in seen_d:
                seen_d.add(parsed.path)
                dirs.append(parsed.path)
    return products, dirs


def extract_characteristics(soup):
    """Récupère tous les couples label/valeur des tableaux "Caractéristiques"
    de la fiche produit, quels que soient les champs présents (ils varient
    d'une annonce à l'autre)."""
    heading = None
    for tag in soup.find_all(["h1", "h2", "h3"]):
        if "caractéristique" in tag.get_text(strip=True).lower():
            heading = tag
            break
    if heading is None:
        return {}

    characteristics = {}
    # on parcourt les éléments suivants jusqu'au prochain titre (ex: "Équipements")
    for sibling in heading.find_all_next():
        if sibling.name in ("h1", "h2", "h3"):
            break
        if sibling.name == "table":
            for row in sibling.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).rstrip(":").strip()
                    value = cells[1].get_text(strip=True)
                    if label and value:
                        characteristics[label] = value
    return characteristics


def format_price(value):
    """Insère un espace tous les 3 chiffres pour la partie entière
    (ex: "90000,00" -> "90 000,00"), comme sur le site d'origine."""
    integer_part, decimals = value.split(",")
    reversed_digits = integer_part[::-1]
    grouped = " ".join(reversed_digits[i:i + 3] for i in range(0, len(reversed_digits), 3))
    return f"{grouped[::-1]},{decimals}"


def parse_price(text):
    """Cherche un prix dans un texte. Retourne "35 000,00 € HT" ou None."""
    match = PRICE_RE.search(text)
    if not match:
        return None
    candidate = match.group(1).replace(" ", "").replace(" ", "")
    # Garde-fou : un prix de plusieurs millions est forcément une erreur
    # (deux nombres collés l'un à l'autre).
    integer_part = candidate.split(",")[0]
    if integer_part.isdigit() and int(integer_part) <= 500000:
        return format_price(candidate) + " € HT"
    return None


def parse_price_from_strings(soup):
    """Cherche le prix bloc de texte par bloc de texte (avant la section
    "Caractéristiques") : un nombre qui termine le nom du modèle (ex: "ARION
    460") ne peut ainsi plus se coller au prix du bloc suivant."""
    for text in soup.stripped_strings:
        if "caractéristique" in text.lower():
            break
        price = parse_price(text)
        if price:
            return price
    return None


def classify(url):
    """Déduit (id, catégorie, sous-catégorie) à partir de l'adresse de l'annonce."""
    m = PRODUCT_URL_RE.search(urlparse(url).path)
    segments = [s for s in m.group(1).split("/") if s]
    ad_id = m.group(2)
    category = "tracteurs" if segments and segments[0] == "tracteurs" else "materiels"
    subcategory = None
    if category == "materiels" and segments and segments[-1] != "materiels":
        subcategory = segments[-1]
    return ad_id, category, subcategory


def extract_detail(url, html):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    # get_text(" ", ...) : sans le séparateur, deux blocs de texte collés dans
    # le HTML se retrouvaient fusionnés sans espace ("VALTRAT195").
    model = " ".join(h1.get_text(" ", strip=True).split()) if h1 else None

    if not model:
        # Fiche mal reconnue : on l'écarte plutôt que d'afficher une annonce vide.
        return None

    # Le petit tableau "Puissance / Heures / Référence" est retiré du texte
    # avant de chercher le prix : sinon, pour un prix à 6 chiffres, ses chiffres
    # se collaient au numéro de référence et donnaient un prix absurde.
    # Avant de le retirer, on en lit les lignes : pour les matériels, c'est là
    # que figure le "Type" (Chargeuse, Compacteur, Pulvérisateur...), qui n'est
    # pas dans le tableau "Caractéristiques".
    header = {}
    for table in soup.find_all("table"):
        if "référence" in table.get_text(strip=True).lower():
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    label = cells[0].get_text(" ", strip=True).rstrip(":").strip()
                    value = " ".join(cells[1].get_text(" ", strip=True).split())
                    if label and value:
                        header[label] = value
            table.decompose()
            break

    full_text = " ".join(soup.get_text(" ", strip=True).split())

    # Le prix est avant la section "Caractéristiques" : on limite la recherche
    # à cette zone pour ne pas confondre avec un nombre sans rapport.
    end = full_text.lower().find("caractéristique")
    search_zone = full_text[:end] if end != -1 else full_text[:1500]

    price = parse_price_from_strings(soup) or parse_price(search_zone)

    characteristics = extract_characteristics(soup)

    # Le type n'est conservé que pour les matériels (les tracteurs n'en ont pas)
    if classify(url)[1] == "materiels" and header.get("Type") and "Type" not in characteristics:
        characteristics = {"Type": header["Type"], **characteristics}

    # image principale : première photo de la galerie (dossier /media/),
    # en excluant le logo
    image = None
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "/media/" in src and "logo" not in src.lower():
            image = urljoin(url, src)
            break

    ad_id, category, subcategory = classify(url)
    return {
        "id": ad_id,
        "category": category,
        "subcategory": subcategory,
        "url": url,
        "model": model,
        "brand": characteristics.get("Marque"),
        "power": characteristics.get("Puissance"),
        "hours": characteristics.get("Heures"),
        "price": price,
        "image": image,
        "characteristics": characteristics,
    }


def crawl_root(root_path, report):
    """Lit toutes les pages d'une liste (page 1, page-2.html, ...).
    Retourne (adresses d'annonces, pages de sous-catégories repérées)."""
    products, dirs = [], []
    seen = set()
    pages_read = 0
    for page in range(1, MAX_PAGES + 1):
        url = page_url(root_path, page)
        try:
            html = fetch(url)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                break   # fin normale de la liste
            report["errors"].append(f"{url}: HTTP {status}")
            raise SiteUnavailable(f"{url} : le site répond HTTP {status}")
        except requests.RequestException as exc:
            report["errors"].append(f"{url}: {exc}")
            raise SiteUnavailable(f"{url} : {exc}")
        pages_read += 1

        page_products, page_dirs = extract_links(url, html)
        if page == 1:
            dirs = page_dirs
        new_products = [u for u in page_products if u not in seen]
        if not new_products:
            break
        for u in new_products:
            seen.add(u)
            products.append(u)
        time.sleep(LIST_DELAY)

    print(f"  {root_path} : {pages_read} page(s), {len(products)} annonce(s)")
    report["roots"].append({"path": root_path, "pages": pages_read, "products": len(products)})
    return products, dirs


def discover_all(report):
    """Parcourt les pages de liste (tracteurs, puis le reste du catalogue) et
    retourne la liste des adresses d'annonces, dans l'ordre du site."""
    extra = [p.strip() for p in os.environ.get("EXTRA_LISTING_PATHS", "").split(",") if p.strip()]
    queue = [(TRACTEURS_PATH, 0), (HUB_PATH, 0)] + [(p, 1) for p in extra]
    visited = set()
    all_urls, seen_urls = [], set()

    while queue and len(visited) < MAX_ROOTS:
        path, depth = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        products, dirs = crawl_root(path, report)
        for u in products:
            if u not in seen_urls:
                seen_urls.add(u)
                all_urls.append(u)
        if depth < MAX_DEPTH:
            for d in dirs:
                if d not in visited:
                    queue.append((d, depth + 1))
    return all_urls


def load_known():
    """Annonces déjà connues (copie de secours enregistrée au passage précédent)."""
    try:
        with open(OUTPUT_ALL, encoding="utf-8") as f:
            data = json.load(f)
        return {l["url"]: l for l in data.get("listings", []) if l.get("url")}
    except (OSError, ValueError, AttributeError):
        return {}


def scrape_all(report, known=None, full=True, weekday=0):
    """Retourne la liste des annonces.
    - full=True : toutes les fiches sont relues (comportement historique) ;
    - full=False : seules les annonces nouvelles, et 1/7 des annonces connues
      (selon leur numéro et le jour de la semaine), sont relues."""
    known = known or {}
    all_urls = discover_all(report)
    print(f"Total : {len(all_urls)} annonces trouvées sur le site")

    listings = []
    counters = {}
    fetched = reused = new = 0
    consecutive_errors = 0
    can_fetch = True
    for url in all_urls:
        ad_id = classify(url)[0]
        old = known.get(url)
        due = full or old is None or (int(ad_id) % REFRESH_CYCLE == weekday)

        detail = None
        if due and can_fetch:
            try:
                html = fetch(url)
                detail = extract_detail(url, html)
                consecutive_errors = 0
                fetched += 1
                if old is None and detail is not None:
                    new += 1
                time.sleep(DETAIL_DELAY)
            except requests.RequestException as exc:
                print(f"Erreur sur {url}: {exc}", file=sys.stderr)
                report["errors"].append(f"{url}: {exc}")
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    can_fetch = False
                    report["interrompu"] = "trop d'erreurs consécutives : le robot n'insiste pas"
                    print("Trop d'erreurs consécutives : le robot cesse d'interroger le site.",
                          file=sys.stderr)
        if detail is None and old is not None:
            detail = dict(old)          # on garde ce qu'on savait déjà
            reused += 1
        if detail is None:
            continue
        cat = detail["category"]
        detail["sort_order"] = counters.get(cat, 0)
        counters[cat] = counters.get(cat, 0) + 1
        listings.append(detail)

    report["fiches_lues"] = fetched
    report["fiches_reprises"] = reused
    report["nouvelles_annonces"] = new
    print(f"Fiches ouvertes : {fetched} (dont {new} nouvelles) | reprises sans toucher au site : {reused}")
    return listings


# ---------------------------------------------------------------- Supabase

def supabase_conf():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    return url, key


def supabase_headers(key, **extra):
    h = {"apikey": key, "Content-Type": "application/json"}
    # Les anciennes clés (service_role) sont des JWT et s'envoient aussi en
    # "Bearer" ; les nouvelles clés secrètes (sb_secret_...) ne vont que dans
    # l'en-tête "apikey".
    if key.startswith("eyJ"):
        h["Authorization"] = f"Bearer {key}"
    h.update(extra)
    return h


def sync_supabase(listings, run_started, report, allow_offline=True):
    conf = supabase_conf()
    if conf is None:
        print("Supabase non configuré (SUPABASE_URL / SUPABASE_SERVICE_KEY) : envoi ignoré.")
        report["supabase"] = "non configuré"
        return
    url, key = conf
    endpoint = f"{url}/rest/v1/listings"

    # 1. Ajout / mise à jour. La colonne "hidden" (annonce masquée depuis la
    #    page d'administration) n'est volontairement jamais envoyée : elle est
    #    donc conservée à chaque passage du robot.
    rows = []
    for l in listings:
        rows.append({
            "id": l["id"], "category": l["category"], "subcategory": l["subcategory"],
            "url": l["url"], "model": l["model"], "brand": l["brand"],
            "power": l["power"], "hours": l["hours"], "price": l["price"],
            "image": l["image"], "characteristics": l["characteristics"],
            "sort_order": l["sort_order"], "online": True, "last_seen": run_started,
        })
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        resp = requests.post(
            f"{endpoint}?on_conflict=id",
            headers=supabase_headers(key, Prefer="resolution=merge-duplicates,return=minimal"),
            data=json.dumps(chunk), timeout=60,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Supabase a refusé l'envoi : {resp.status_code} {resp.text[:300]}")
    print(f"Supabase : {len(rows)} annonces envoyées")

    # 2. Annonces disparues du site : marquées "hors ligne" (jamais supprimées),
    #    seulement si le robot a bien retrouvé l'essentiel de la catégorie.
    offline_report = {}
    if not allow_offline:
        print("Passage interrompu : aucune annonce n'est retirée cette fois.")
        report["supabase"] = "ok"
        report["mises_hors_ligne"] = "ignoré (passage interrompu)"
        return
    for category in sorted({l["category"] for l in listings}):
        scraped = sum(1 for l in listings if l["category"] == category)
        resp = requests.get(
            endpoint,
            params={"select": "id", "category": f"eq.{category}", "online": "eq.true",
                    "last_seen": f"lt.{run_started}"},
            headers=supabase_headers(key, Prefer="count=exact", Range="0-0"), timeout=30,
        )
        stale = 0
        content_range = resp.headers.get("Content-Range", "")
        if "/" in content_range and content_range.split("/")[1].isdigit():
            stale = int(content_range.split("/")[1])
        previously_online = scraped + stale
        if previously_online and scraped < 0.5 * previously_online:
            print(f"ATTENTION : {category} : seulement {scraped} annonces trouvées sur "
                  f"{previously_online} en ligne, mise hors ligne annulée par prudence.",
                  file=sys.stderr)
            offline_report[category] = "annulé (chute suspecte)"
            continue
        if stale:
            patch = requests.patch(
                endpoint,
                params={"category": f"eq.{category}", "online": "eq.true",
                        "last_seen": f"lt.{run_started}"},
                headers=supabase_headers(key, Prefer="return=minimal"),
                data=json.dumps({"online": False}), timeout=30,
            )
            if patch.status_code >= 300:
                raise RuntimeError(f"Mise hors ligne refusée : {patch.status_code} {patch.text[:300]}")
        offline_report[category] = stale
        print(f"Supabase : {category} : {stale} annonce(s) retirée(s) du site")
    report["supabase"] = "ok"
    report["mises_hors_ligne"] = offline_report


# -------------------------------------------------------------------- main

def main():
    run_started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {"started_at": run_started, "roots": [], "errors": []}

    full = os.environ.get("FULL_REFRESH", "").strip().lower() in ("1", "true", "yes", "oui")
    weekday = datetime.now(timezone.utc).weekday()
    report["mode"] = "complet" if full else "léger"
    print(f"Mode {report['mode']}")
    try:
        listings = scrape_all(report, known=load_known(), full=full, weekday=weekday)
    except SiteUnavailable as exc:
        print(f"Le site ne répond pas normalement ({exc}). Rien n'est modifié, "
              "le robot réessaiera au prochain passage.", file=sys.stderr)
        report["interrompu"] = str(exc)
        with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        sys.exit(1)
    by_category = {}
    for l in listings:
        by_category[l["category"]] = by_category.get(l["category"], 0) + 1
    report["counts"] = by_category
    print(f"Total : {len(listings)} annonces récupérées {by_category}")

    if not listings:
        print("Aucune annonce récupérée : les fichiers existants sont conservés.", file=sys.stderr)
        sys.exit(1)

    tractors = [l for l in listings if l["category"] == "tracteurs"]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with open(OUTPUT_ALL, "w", encoding="utf-8") as f:
        json.dump({"updated_at": now, "count": len(listings), "counts": by_category,
                   "listings": listings}, f, ensure_ascii=False, indent=2)

    # Ancien format (tracteurs seuls) : garde l'ancien écran fonctionnel
    if tractors:
        legacy = [{k: t[k] for k in ("url", "model", "brand", "power", "hours", "price",
                                     "image", "characteristics")} for t in tractors]
        with open(OUTPUT_TRACTORS, "w", encoding="utf-8") as f:
            json.dump({"updated_at": now, "count": len(legacy), "tractors": legacy},
                      f, ensure_ascii=False, indent=2)

    try:
        sync_supabase(listings, run_started, report, allow_offline=not report.get("interrompu"))
    except Exception as exc:  # on garde les fichiers JSON même si Supabase échoue
        print(f"ERREUR Supabase : {exc}", file=sys.stderr)
        report["supabase"] = f"erreur : {exc}"

    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if str(report.get("supabase", "")).startswith("erreur"):
        sys.exit(1)


if __name__ == "__main__":
    main()
