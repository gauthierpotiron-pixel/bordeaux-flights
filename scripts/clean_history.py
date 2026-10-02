"""Nettoie l'historique exporté du Google Sheets (data/raw) vers data/clean.

Étapes :
  1. collected_at : le texte jj/mm/aaaa est relu correctement (le Sheets avait inversé jour et mois).
  2. Horaires remis au format HH:MM, retard recalculé depuis std et atd.
  3. Doublons : les passages successifs du collecteur sur un même vol sont fusionnés
     (on garde la copie la plus récente et on complète ses trous avec les autres).
  4. Météo : remplacée pour tous les vols par la prévision Open-Meteo de l'heure du départ
     (même source que le collecteur, mise en cache dans data/raw/open_meteo_hourly.csv).
  5. Partages de code : un seul numéro de vol par départ physique est marqué principal.

Usage : python3 scripts/clean_history.py [--refresh-weather]
Aucune dépendance hors bibliothèque standard.
"""
import csv
import json
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CLEAN = ROOT / "data" / "clean"

LAT, LON = 44.83, -0.71
WEATHER_CACHE = RAW / "open_meteo_hourly.csv"
WEATHER_VARS = ["temperature_2m", "precipitation", "wind_speed_10m", "wind_gusts_10m", "visibility", "weather_code"]

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


def parse_fr_datetime(s):
    """'05/04/2026 17:18:13' (jour/mois) -> '2026-04-05 17:18:13'."""
    return datetime.strptime(s, "%d/%m/%Y %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")


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


# ── Météo ──────────────────────────────────────────────

def fetch_weather(start, end):
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}&start_date={start}&end_date={end}"
        f"&hourly={','.join(WEATHER_VARS)}&timezone=Europe%2FParis"
    )
    with urllib.request.urlopen(url, timeout=60) as r:
        h = json.load(r)["hourly"]
    rows = [{"time": t, **{v: h[v][i] for v in WEATHER_VARS}} for i, t in enumerate(h["time"])]
    write_csv(WEATHER_CACHE, rows, ["time"] + WEATHER_VARS)


def load_weather():
    out = {}
    for r in read_csv(WEATHER_CACHE):
        if r["temperature_2m"] == "":
            continue
        out[r["time"][:13]] = r  # clé 'AAAA-MM-JJTHH'
    return out


def weather_fields(w):
    if not w:
        return {k: "" for k in ("weather_temp", "weather_precip", "weather_wind", "weather_gusts",
                                "weather_visibility", "weather_code", "weather_label")}
    code = int(float(w["weather_code"])) if w["weather_code"] != "" else None
    return {
        "weather_temp": w["temperature_2m"],
        "weather_precip": w["precipitation"],
        "weather_wind": w["wind_speed_10m"],
        "weather_gusts": w["wind_gusts_10m"],
        "weather_visibility": w["visibility"],
        "weather_code": "" if code is None else code,
        "weather_label": weather_label(code),
    }


# ── Départs (onglet scheduled) ─────────────────────────

FLIGHT_FIELDS = [
    "date", "flight_iata", "flight_icao", "airline", "destination", "destination_iata",
    "std", "atd", "delay_min", "delay_flag", "status", "codeshares", "aircraft_type", "aircraft_reg",
    "non_schengen", "weather_temp", "weather_precip", "weather_wind", "weather_gusts",
    "weather_visibility", "weather_code", "weather_label", "school_holiday", "day_of_week",
    "is_commercial", "departure_id", "is_primary", "n_snapshots", "first_collected_at", "last_collected_at",
]


def flight_key(r):
    ident = r["flight_iata"] or r["flight_icao"] or f"{r['airline']}|{r['std']}|{r['destination_iata']}"
    return (r["date"], ident)


def merge_snapshots(rows):
    """Copie la plus récente en base, trous comblés par les copies plus anciennes."""
    rows = sorted(rows, key=lambda r: r["collected_at"], reverse=True)
    out = dict(rows[0])
    for col in out:
        if out[col] == "":
            out[col] = next((r[col] for r in rows[1:] if r[col] != ""), "")
    out["n_snapshots"] = len(rows)
    out["first_collected_at"] = rows[-1]["collected_at"]
    out["last_collected_at"] = rows[0]["collected_at"]
    return out


def mark_primary(flights):
    """Un seul numéro par départ physique (date, heure, destination) est principal."""
    groups = defaultdict(list)
    for f in flights:
        f["departure_id"] = f"{f['date']}|{f['std']}|{f['destination_iata']}"
        groups[f["departure_id"]].append(f)
    for group in groups.values():
        def score(f):
            listed = set(f["codeshares"].replace(" ", "").replace("|", ",").split(","))
            others = {g["flight_iata"] for g in group if g is not f}
            return (others <= listed, f["atd"] != "", f["is_commercial"] == 1, f["flight_iata"])
        best = max(group, key=score)
        for f in group:
            f["is_primary"] = int(f is best)


def clean_flights(weather):
    raw = read_csv(RAW / "scheduled.csv")
    for r in raw:
        r["collected_at"] = parse_fr_datetime(r["collected_at"])
        r["std"] = hhmm(r["std"])
        r["atd"] = hhmm(r["atd"])

    groups = defaultdict(list)
    for r in raw:
        groups[flight_key(r)].append(r)

    flights = []
    for rows in groups.values():
        f = merge_snapshots(rows)
        if f["atd"]:
            d = minutes(f["atd"]) - minutes(f["std"])
            if d < -720:  # départ après minuit
                d += 1440
            f["delay_min"] = d
            f["delay_flag"] = "" if DELAY_MIN_OK <= d <= DELAY_MAX_OK else "outlier"
        else:
            f["delay_min"] = ""
            f["delay_flag"] = ""
        f.update(weather_fields(weather.get(f"{f['date']}T{f['std'][:2]}")))
        f["is_commercial"] = int(bool(f["flight_iata"]))
        flights.append(f)

    mark_primary(flights)
    flights.sort(key=lambda f: (f["date"], f["std"], f["flight_iata"] or f["flight_icao"]))
    return raw, flights


# ── Arrivées (onglet arrivals) ─────────────────────────

ARRIVAL_FIELDS = ["flight", "origin", "origin_iata", "arr_scheduled", "arr_actual", "delay_min",
                  "status", "n_snapshots", "last_collected_at"]

STATUS_MAP = {"PRéVU": "prevu", "EN ROUTE": "en_route", "ARRIVéE": "arrivee",
              "BOARDING": "boarding", "ANNULé": "annule"}


def iso_minute(s):
    """'2026-05-16 0:05' -> '2026-05-16 00:05'."""
    if not s:
        return ""
    d, t = s.split(" ")
    return f"{d} {hhmm(t)}"


def clean_arrivals():
    raw = read_csv(RAW / "arrivals.csv")
    groups = defaultdict(list)
    for r in raw:
        r["collected_at"] = iso_minute(r["collected_at"])
        r["arr_scheduled"] = iso_minute(r["arr_scheduled"])
        r["arr_actual"] = iso_minute(r["arr_actual"])
        r["status"] = STATUS_MAP.get(r["status"], r["status"].lower())
        groups[(r["flight"], r["arr_scheduled"])].append(r)

    arrivals = []
    for rows in groups.values():
        a = merge_snapshots(rows)
        name, _, code = a["origin"].rpartition(" [")
        a["origin"], a["origin_iata"] = (name, code.rstrip("]")) if code else (a["origin"], "")
        a["last_collected_at"] = a["collected_at"]
        arrivals.append(a)
    arrivals.sort(key=lambda a: (a["arr_scheduled"], a["flight"]))
    return raw, arrivals


# ── Rapport ────────────────────────────────────────────

def pct(n, total):
    return f"{100 * n / total:.0f} %" if total else "0 %"


def report(raw_f, flights, raw_a, arrivals):
    dates = sorted({f["date"] for f in flights})
    first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    all_days = {date.fromordinal(o).isoformat() for o in range(first.toordinal(), last.toordinal() + 1)}
    missing = sorted(all_days - set(dates))
    prim = [f for f in flights if f["is_primary"] and f["is_commercial"]]
    labelled = [f for f in prim if f["delay_min"] != "" and not f["delay_flag"] and f["status"] != "cancelled"]
    n = len(flights)
    lines = [
        "# Rapport de nettoyage",
        "",
        f"Source : export du Google Sheets du {(RAW / 'EXPORTED_AT.txt').read_text().strip()}. "
        "Généré par `scripts/clean_history.py`.",
        "",
        "## Départs (`flights.csv`)",
        "",
        "| | Nombre |",
        "|---|---|",
        f"| Lignes brutes | {len(raw_f)} |",
        f"| Vols après fusion des doublons | {n} |",
        f"| Lignes fusionnées | {len(raw_f) - n} |",
        f"| Vols commerciaux (numéro IATA) | {sum(f['is_commercial'] for f in flights)} |",
        f"| Départs physiques commerciaux (sans doublon de partage de code) | {len(prim)} |",
        f"| dont avec retard réel exploitable | {len(labelled)} ({pct(len(labelled), len(prim))}) |",
        f"| Annulés | {sum(f['status'] == 'cancelled' for f in prim)} |",
        f"| Retards signalés aberrants (hors {DELAY_MIN_OK} à +{DELAY_MAX_OK} min) | "
        f"{sum(f['delay_flag'] == 'outlier' for f in flights)} |",
        f"| Avec météo | {sum(f['weather_temp'] != '' for f in flights)} ({pct(sum(f['weather_temp'] != '' for f in flights), n)}) |",
        f"| Avec immatriculation | {sum(f['aircraft_reg'] != '' for f in flights)} ({pct(sum(f['aircraft_reg'] != '' for f in flights), n)}) |",
        "",
        f"Période : du {dates[0]} au {dates[-1]}, {len(dates)} jours collectés.",
        "",
        f"Jours sans aucune collecte ({len(missing)}) : {', '.join(missing) or 'aucun'}. "
        "Ils ne sont pas récupérables avec le plan gratuit AviationStack.",
        "",
        "Retard à 15 min ou plus, par mois (départs commerciaux principaux avec retard réel) :",
        "",
        "| Mois | Vols | Part en retard ≥ 15 min |",
        "|---|---|---|",
    ]
    by_month = defaultdict(list)
    for f in labelled:
        by_month[f["date"][:7]].append(int(f["delay_min"]) >= 15)
    for m in sorted(by_month):
        v = by_month[m]
        lines.append(f"| {m} | {len(v)} | {pct(sum(v), len(v))} |")
    a_days = Counter(a["arr_scheduled"][:10] for a in arrivals)
    lines += [
        "",
        "## Arrivées (`arrivals.csv`)",
        "",
        f"{len(raw_a)} lignes brutes, {len(arrivals)} vols après fusion, "
        f"{sum(a['arr_actual'] != '' for a in arrivals)} avec heure d'arrivée réelle. "
        f"Jours couverts : {', '.join(f'{d} ({c})' for d, c in sorted(a_days.items()))}.",
        "",
    ]
    (CLEAN / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main():
    if "--refresh-weather" in sys.argv or not WEATHER_CACHE.exists():
        dates = sorted(r["date"] for r in read_csv(RAW / "scheduled.csv"))
        fetch_weather(dates[0], dates[-1])
    weather = load_weather()
    raw_f, flights = clean_flights(weather)
    write_csv(CLEAN / "flights.csv", flights, FLIGHT_FIELDS)
    raw_a, arrivals = clean_arrivals()
    write_csv(CLEAN / "arrivals.csv", arrivals, ARRIVAL_FIELDS)
    report(raw_f, flights, raw_a, arrivals)


if __name__ == "__main__":
    main()
