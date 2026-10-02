"""Chemins, lecture/écriture CSV et petites fonctions partagées par les scripts."""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
CLEAN = DATA / "clean"

LAT, LON = 44.83, -0.71  # Bordeaux-Mérignac

FLIGHT_FIELDS = [
    "date", "flight_iata", "flight_icao", "airline", "destination", "destination_iata",
    "std", "atd", "delay_min", "delay_flag", "status", "codeshares", "aircraft_type", "aircraft_reg",
    "non_schengen", "weather_temp", "weather_precip", "weather_wind", "weather_gusts",
    "weather_visibility", "weather_code", "weather_label", "school_holiday", "day_of_week",
    "is_commercial", "departure_id", "is_primary", "n_snapshots", "first_collected_at", "last_collected_at",
    "airport_status", "est_departure",
]

# Retards hors de cette plage : gardés mais signalés (souvent de l'aviation privée).
DELAY_MIN_OK, DELAY_MAX_OK = -30, 600


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def hhmm(t):
    """'8:50' ou '8:50:00' -> '08:50'. Vide -> ''."""
    if not t:
        return ""
    h, m = t.split(":")[:2]
    return f"{int(h):02d}:{int(m):02d}"


def minutes(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def delay_between(std, atd):
    """Retard en minutes entre deux heures HH:MM, en gérant le passage de minuit."""
    d = minutes(atd) - minutes(std)
    if d < -720:
        d += 1440
    return d


def delay_flag(d):
    return "" if DELAY_MIN_OK <= d <= DELAY_MAX_OK else "outlier"


def weather_label(code):
    """Libellés identiques à ceux du collecteur (0 Clair, 1-3 Nuageux, 51-55 Pluie)."""
    if code is None:
        return ""
    code = int(code)
    if code == 0:
        return "Clair"
    if code <= 3:
        return "Nuageux"
    if code in (45, 48):
        return "Brouillard"
    if 71 <= code <= 77 or code in (85, 86):
        return "Neige"
    if code >= 95:
        return "Orage"
    return "Pluie"


# Compagnies de fret (DHL, UPS, FedEx…) : leurs vols ne concernent pas les passagers.
CARGO_PREFIXES = {"QY", "D0", "DJ", "5X", "FX", "3V", "QT", "ES", "X5"}


def is_commercial(flight_iata, airline=""):
    """Vol passagers régulier : numéro complet (compagnie + numéro), hors fret.
    Exclut l'aviation privée, les codes seuls et les compagnies cargo."""
    if not re.match(r"^[A-Z0-9]{2}\d{1,4}[A-Z]?$", flight_iata or ""):
        return 0
    return int(flight_iata[:2] not in CARGO_PREFIXES and "cargo" not in (airline or "").lower())


def split_codes(codeshares):
    return [c.strip().upper() for c in re.split(r"[|,]", codeshares or "") if c.strip()]


def operator_scores(flights):
    """Pour chaque compagnie (préfixe IATA) et destination : part de ses apparitions comme
    numéro de vol plutôt que comme simple partage de code chez une autre. Proche de 1 pour
    la compagnie qui opère (Air France vers Paris, Vueling vers Barcelone), proche de 0 pour
    celle qui ne fait que vendre des places (SAS, Qatar). L'ancien collecteur notait tous les
    numéros d'un même vol sans dire lequel opère : c'est ce qui permet de le retrouver."""
    as_flight, as_code = {}, {}
    for f in flights:
        dest = f["destination_iata"]
        if f["flight_iata"]:
            k = (f["flight_iata"][:2], dest)
            as_flight[k] = as_flight.get(k, 0) + 1
        for c in split_codes(f["codeshares"]):
            k = (c[:2], dest)
            as_code[k] = as_code.get(k, 0) + 1
    return {k: (as_flight.get(k, 0) + 1) / (as_flight.get(k, 0) + as_code.get(k, 0) + 2)
            for k in set(as_flight) | set(as_code)}


def assign_operators(flights):
    """Renomme les vols enregistrés seulement sous le numéro d'un partenaire commercial
    (SK9715 pour le Bordeaux-Paris d'Air France) avec le numéro de la compagnie qui opère,
    quand ce numéro figure dans les partages de code. Sans effet si on le relance."""
    scores = operator_scores(flights)
    names = {}
    for f in flights:
        if f["flight_iata"]:
            names.setdefault(f["flight_iata"][:2], {}).setdefault(f["airline"], 0)
            names[f["flight_iata"][:2]][f["airline"]] += 1
    taken = {(f["date"], f["flight_iata"]) for f in flights}
    # Vol opéré habituellement à cette heure vers cette destination (si les numéros
    # notés ce jour-là ne sont que ceux de partenaires).
    usual = {}
    for f in flights:
        if f["flight_iata"] and scores.get((f["flight_iata"][:2], f["destination_iata"]), 0) >= 0.5:
            slot = usual.setdefault((f["destination_iata"], f["std"]), {})
            slot[f["flight_iata"]] = slot.get(f["flight_iata"], 0) + 1
    renamed = 0
    for f in flights:
        if not f["flight_iata"] or f.get("airport_status"):
            continue
        dest = f["destination_iata"]
        if scores.get((f["flight_iata"][:2], dest), 0.5) >= 0.3:
            continue
        codes = split_codes(f["codeshares"])
        best = max(codes, key=lambda c: scores.get((c[:2], dest), 0), default=None)
        if not best or scores.get((best[:2], dest), 0) < 0.5:
            slot = usual.get((dest, f["std"]), {})
            best = max(slot, key=slot.get, default=None)
        if not best or (f["date"], best) in taken:
            continue
        taken.add((f["date"], best))
        f["codeshares"] = "|".join([f["flight_iata"]] + [c for c in codes if c != best])
        f["flight_iata"], f["flight_icao"] = best, ""
        if best[:2] in names:
            f["airline"] = max(names[best[:2]], key=names[best[:2]].get)
        renamed += 1
    return renamed


def mark_primary(flights):
    """Un seul numéro par départ physique (date, heure, destination) est principal :
    celui de la compagnie qui opère le vol, pas d'un partenaire qui vend des places.
    Ordre de préférence : le numéro affiché par l'aéroport (il liste toujours l'opérateur),
    puis le score d'opérateur de la compagnie vers cette destination."""
    scores = operator_scores(flights)
    groups = {}
    for f in flights:
        f["departure_id"] = f"{f['date']}|{f['std']}|{f['destination_iata']}"
        groups.setdefault(f["departure_id"], []).append(f)
    for group in groups.values():
        def score(f):
            op = scores.get((f["flight_iata"][:2], f["destination_iata"]), 0) if f["flight_iata"] else -1
            return (bool(f.get("airport_status")), op, f["atd"] != "", f["flight_iata"])
        best = max(group, key=score)
        for f in group:
            f["is_primary"] = int(f is best)
