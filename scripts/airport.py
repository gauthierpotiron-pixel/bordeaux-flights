"""Lit le tableau des départs du jour publié par l'aéroport de Bordeaux.

Seule la page par défaut est lue (son robots.txt l'autorise ; les variantes avec
paramètres, comme le jour suivant, sont interdites et ne sont pas utilisées).
Elle liste les vols à partir de l'heure en cours : lue tôt le matin, elle donne
tout le programme de la journée. Aucun quota, une requête par passage.
"""
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from html.parser import HTMLParser

from common import hhmm

URL = "https://www.bordeaux.aeroport.fr/vols-destinations/arrivees-departs-du-jour?w=out"
USER_AGENT = "BOD-flights/1.0 (+https://gauthierpotiron-pixel.github.io/bordeaux-flights/)"


class _Table(HTMLParser):
    """Récupère le texte de chaque cellule des lignes du tableau, et la date affichée.
    Une cellule peut contenir plusieurs valeurs séparées par <br> (partages de code) :
    elles sont renvoyées sous forme de liste."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None
        self.in_tbody = self.in_date = False
        self.date = None

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self.row = []
        elif tag == "td" and self.row is not None:
            self.cell = [[]]
        elif tag == "br" and self.cell is not None:
            self.cell.append([])
        elif tag == "span" and ("id", "filter-date") in attrs:
            self.in_date = True

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            parts = [re.sub(r"\s+", " ", "".join(p)).strip() for p in self.cell]
            self.row.append([p for p in parts if p] or [""])
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "tbody":
            self.in_tbody = False
        elif tag == "span":
            self.in_date = False

    def handle_data(self, data):
        if self.in_date and self.date is None and data.strip():
            self.date = data.strip()
        if self.cell is not None:
            self.cell[-1].append(data)


def fetch_page():
    req = urllib.request.Request(URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def reference_maps(flights):
    """Tables tirées de l'historique : préfixe OACI -> IATA, compagnie et destination habituelles."""
    prefix = defaultdict(Counter)
    airline = defaultdict(Counter)
    route = {}
    for f in flights:
        iata, icao = f["flight_iata"], f["flight_icao"]
        a, b = re.match(r"^([A-Z]{3})(\d+)$", icao or ""), re.match(r"^([A-Z0-9]{2})(\d+)$", iata or "")
        if a and b and a[2] == b[2]:
            prefix[a[1]][b[1]] += 1
        if iata:
            airline[iata[:2]][f["airline"]] += 1
            route[iata] = (f["destination"], f["destination_iata"])  # le plus récent gagne
    return ({k: v.most_common(1)[0][0] for k, v in prefix.items()},
            {k: v.most_common(1)[0][0] for k, v in airline.items()}, route)


def to_iata(number, prefix_map):
    """'EJU1807' -> 'EC1807' ; 'V72134' reste tel quel."""
    m = re.match(r"^([A-Z]{3})(\d+)$", number)
    if m and m[1] in prefix_map:
        return prefix_map[m[1]] + m[2]
    return number


def parse(page, flights, collected_at):
    """Page HTML -> lignes au format de flights.csv."""
    table = _Table()
    table.feed(page)
    if not table.date:
        raise ValueError("Date introuvable sur la page de l'aéroport : la structure a peut-être changé.")
    day = datetime.strptime(table.date, "%d/%m/%Y").date().isoformat()
    prefix_map, airline_map, route = reference_maps(flights)
    rows = []
    for cells in table.rows:
        if len(cells) < 4 or not re.match(r"^\d{1,2}:\d{2}$", cells[0][0]):
            continue
        numbers = [n.replace(" ", "").upper() for n in cells[2]]
        numbers = [n for n in numbers if re.match(r"^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$", n) and not n.isdigit()]
        if not numbers:
            continue
        status = cells[-1][0]
        flight = to_iata(numbers[0], prefix_map)  # le premier numéro est le vol opéré
        destination, destination_iata = route.get(flight, (cells[1][0].title(), ""))
        est = re.search(r"(\d{1,2}:\d{2})", status) if status.lower().startswith("prévu") else None
        rows.append({
            "date": day, "flight_iata": flight, "flight_icao": "",
            "airline": airline_map.get(flight[:2], cells[3][0]),
            "destination": destination, "destination_iata": destination_iata,
            "std": hhmm(cells[0][0]), "atd": "",
            "status": "cancelled" if "annul" in status.lower() else "",
            "airport_status": status, "est_departure": hhmm(est[1]) if est else "",
            "aircraft_type": "", "aircraft_reg": "",
            "codeshares": "|".join(to_iata(n, prefix_map) for n in numbers[1:]), "collected_at": collected_at,
        })
    return rows


def fetch(flights, collected_at):
    rows = parse(fetch_page(), flights, collected_at)
    print(f"Aéroport : {len(rows)} départs lus, dont {sum(1 for r in rows if r['est_departure'])} avec retard annoncé.")
    return rows
