#!/usr/bin/env python3
"""
Récupère automatiquement la liste des tracteurs en vente sur
nauleau-agricole.com et l'enregistre dans data/tracteurs.json.

Ce script est conçu pour être robuste face aux changements mineurs de mise
en page : il repère les annonces via leur URL (qui contient toujours un
numéro d'annonce, ex: /tracteurs-materiels/tracteurs/10624-fendt-311-profi.html)
plutôt que via des classes CSS qui peuvent changer.
"""

import json
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.nauleau-agricole.com"
LISTING_PATH = "/tracteurs-materiels/tracteurs/"
OUTPUT_FILE = "data/tracteurs.json"
MAX_PAGES = 30  # garde-fou pour ne jamais boucler indéfiniment
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NauleauVitrineBot/1.0; "
                  "+https://github.com/) "
}

# Une annonce est reconnue par ce type d'URL : .../123456-nom-du-modele.html
PRODUCT_URL_RE = re.compile(r"/tracteurs-materiels/tracteurs/\d+-[^/]+\.html$")

PRICE_RE = re.compile(r"(\d{1,3}(?:[\s\u00a0]\d{3})*,\d{2})\s*€")


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def page_url(page_number):
    if page_number == 1:
        return urljoin(BASE_URL, LISTING_PATH)
    return urljoin(BASE_URL, f"{LISTING_PATH}page-{page_number}.html")


def extract_listing_urls(url, html):
    """Phase 1 : repère juste les URLs des annonces sur une page de liste."""
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    seen = set()
    for a in soup.find_all("a", href=True):
        full_url = urljoin(url, a["href"])
        if PRODUCT_URL_RE.search(full_url) and full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)
    return urls


def extract_characteristics(soup):
    """Récupère tous les couples label/valeur des tableaux "Caractéristiques"
    de la fiche produit (Marque, Modèle, État, Année, Roues motrices,
    Boîte de vitesses, Dimension pneus, Usure pneus, etc.) — quels que
    soient les champs présents, puisqu'ils varient d'une annonce à l'autre.
    """
    heading = None
    for tag in soup.find_all(["h1", "h2", "h3"]):
        if "caractéristique" in tag.get_text(strip=True).lower():
            heading = tag
            break
    if heading is None:
        return {}

    characteristics = {}
    node = heading
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
    grouped = " ".join(reversed_digits[i:i+3] for i in range(0, len(reversed_digits), 3))
    formatted_integer = grouped[::-1]
    return f"{formatted_integer},{decimals}"


def extract_detail(url, html):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    # get_text(" ", ...) : sans le séparateur, deux blocs de texte collés
    # dans le HTML (ex: la marque et le modèle dans des balises différentes)
    # se retrouvaient fusionnés sans espace ("VALTRAT195" au lieu de
    # "VALTRA T195").
    model = " ".join(h1.get_text(" ", strip=True).split()) if h1 else None

    if not model:
        # Fiche mal reconnue (page qui ne correspond pas au format attendu) :
        # on l'écarte plutôt que d'afficher une annonce vide à l'écran.
        return None

    # Le petit tableau "Puissance / Heures / Référence" juste avant le prix
    # posait un problème : quand le prix atteint 6 chiffres (100 000 € et
    # plus), ses chiffres pouvaient se recoller à la fin du numéro de
    # référence et former un nombre à 9 chiffres totalement faux (rejeté
    # ensuite par le garde-fou, d'où le "Nous consulter" à tort). On retire
    # ce tableau du texte avant de chercher le prix, pour éliminer tout
    # risque de mélange.
    for table in soup.find_all("table"):
        if "référence" in table.get_text(strip=True).lower():
            table.decompose()
            break

    full_text = " ".join(soup.get_text(" ", strip=True).split())

    # Le prix se trouve juste après ce tableau, avant la section
    # "Caractéristiques". On limite la recherche à cette zone pour éviter
    # qu'un nombre sans rapport (ailleurs sur la page) ne soit confondu
    # avec le prix.
    end = full_text.lower().find("caractéristique")
    search_zone = full_text[:end] if end != -1 else full_text[:1500]

    price = None
    price_match = PRICE_RE.search(search_zone)
    if price_match:
        candidate = price_match.group(1).replace(" ", "").replace("\u00a0", "")
        # Garde-fou : un tracteur d'occasion ne vaut jamais plusieurs
        # millions d'euros. Si on tombe sur un nombre absurde, c'est que
        # deux nombres se sont mélangés quelque part — on préfère ne rien
        # afficher plutôt qu'un prix faux.
        integer_part = candidate.split(",")[0]
        if integer_part.isdigit() and int(integer_part) <= 500000:
            price = format_price(candidate) + " € HT"

    characteristics = extract_characteristics(soup)

    # image principale : la première photo de la galerie produit, dans le
    # dossier /media/, en excluant le logo et les images de la carte
    image = None
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "/media/" in src and "logo" not in src.lower():
            image = urljoin(url, src)
            break

    return {
        "url": url,
        "model": model,
        "brand": characteristics.get("Marque"),
        "power": characteristics.get("Puissance"),
        "hours": characteristics.get("Heures"),
        "price": price,
        "image": image,
        "characteristics": characteristics,
    }


def scrape_all():
    # Phase 1 : lister toutes les URLs d'annonces sur les pages de liste
    all_urls = []
    seen_urls = set()
    for page in range(1, MAX_PAGES + 1):
        url = page_url(page)
        try:
            html = fetch(url)
        except requests.HTTPError:
            break
        except requests.RequestException as exc:
            print(f"Erreur réseau sur {url}: {exc}", file=sys.stderr)
            break

        urls = extract_listing_urls(url, html)
        if not urls:
            break

        new_count = 0
        for u in urls:
            if u not in seen_urls:
                seen_urls.add(u)
                all_urls.append(u)
                new_count += 1

        print(f"Page {page}: {len(urls)} annonces trouvées, {new_count} nouvelles")

        if new_count == 0:
            break

        time.sleep(1)

    print(f"Total : {len(all_urls)} fiches à récupérer")

    # Phase 2 : aller chercher le détail complet de chaque annonce
    tractors = []
    for i, url in enumerate(all_urls, start=1):
        try:
            html = fetch(url)
            detail = extract_detail(url, html)
            if detail is not None:
                tractors.append(detail)
        except requests.RequestException as exc:
            print(f"Erreur sur {url}: {exc}", file=sys.stderr)
            continue

        if i % 10 == 0:
            print(f"  {i}/{len(all_urls)} fiches récupérées")

        time.sleep(0.5)

    return tractors


def main():
    tractors = scrape_all()
    print(f"Total : {len(tractors)} tracteurs récupérés")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "count": len(tractors),
            "tractors": tractors,
        }, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
