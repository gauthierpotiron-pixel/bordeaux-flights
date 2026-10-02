"""Chemins, lecture/écriture CSV et petites fonctions partagées par les scripts."""
import csv
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


def mark_primary(flights):
    """Un seul numéro par départ physique (date, heure, destination) est principal."""
    groups = {}
    for f in flights:
        f["departure_id"] = f"{f['date']}|{f['std']}|{f['destination_iata']}"
        groups.setdefault(f["departure_id"], []).append(f)
    for group in groups.values():
        def score(f):
            listed = set(f["codeshares"].replace(" ", "").replace("|", ",").split(","))
            others = {g["flight_iata"] for g in group if g is not f}
            return (others <= listed, f["atd"] != "", str(f["is_commercial"]) == "1", f["flight_iata"])
        best = max(group, key=score)
        for f in group:
            f["is_primary"] = int(f is best)
