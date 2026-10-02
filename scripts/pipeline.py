"""Chaîne automatique lancée par GitHub Actions : collecte, enrichissement, prédiction.

1. Collecte les départs de BOD via AviationStack (plan gratuit : 100 appels par mois,
   compteur dans data/api_usage.json pour ne jamais dépasser).
2. Fusionne avec l'historique data/clean/flights.csv.
3. Ajoute la météo prévue (Open-Meteo), les vacances scolaires et le statut Schengen.
4. Réentraîne le modèle et prédit les vols du jour et du lendemain.
5. Écrit data/predictions.json (lu par le site) et data/predictions_log.csv (historique
   des prédictions, pour comparer ensuite avec les retards réels).

Usage :
  AVIATIONSTACK_KEY=... python3 scripts/pipeline.py
  python3 scripts/pipeline.py --from-file reponse.json   (rejoue une réponse enregistrée, sans appel)
  python3 scripts/pipeline.py --no-collect               (recalcule seulement les prédictions)
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import model
from common import (CLEAN, DATA, FLIGHT_FIELDS, LAT, LON, delay_between, delay_flag, hhmm, mark_primary,
                    read_csv, weather_label, write_csv)

PARIS = ZoneInfo("Europe/Paris")
FLIGHTS_CSV = CLEAN / "flights.csv"
USAGE_JSON = DATA / "api_usage.json"
PREDICTIONS_JSON = DATA / "predictions.json"
PREDICTIONS_LOG = DATA / "predictions_log.csv"
HOLIDAYS_JSON = DATA / "ref" / "school_holidays_zone_a.json"

MONTHLY_QUOTA = 100
RUNS_PER_DAY = 3  # doit correspondre au cron de .github/workflows/pipeline.yml
API_URL = "https://api.aviationstack.com/v1/flights"

LOG_FIELDS = ["date", "flight_iata", "std", "predicted_at", "prediction_type", "probabilite_retard", "risk_level"]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# Seuils de probabilité (retard ≥ 15 min) pour les 4 niveaux affichés sur le site.
RISK_LEVELS = [(0.35, "green", "Faible risque de retard"), (0.50, "yellow", "Retard possible"),
               (0.65, "orange", "Retard probable"), (1.01, "red", "Retard très probable")]


def now_paris():
    override = os.environ.get("BOD_NOW")  # pour les tests : 'AAAA-MM-JJ HH:MM'
    if override:
        return datetime.strptime(override, "%Y-%m-%d %H:%M").replace(tzinfo=PARIS)
    return datetime.now(PARIS)


def get_json(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


# ── 1. Collecte ────────────────────────────────────────

def load_usage(month):
    usage = json.loads(USAGE_JSON.read_text()) if USAGE_JSON.exists() else {}
    return usage, usage.get(month, 0)


def save_usage(usage):
    USAGE_JSON.write_text(json.dumps(usage, indent=2, sort_keys=True) + "\n")


def calls_reserved(now):
    """Appels à garder pour les passages restants du mois (un par passage)."""
    next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
    days_left = (next_month.date() - now.date()).days - 1
    return days_left * RUNS_PER_DAY


def fetch_aviationstack(now):
    key = os.environ.get("AVIATIONSTACK_KEY")
    if not key:
        sys.exit("AVIATIONSTACK_KEY manquant : ajoute-le dans les secrets GitHub du repo.")
    month = now.strftime("%Y-%m")
    usage, used = load_usage(month)
    records, offset = [], 0
    while True:
        # Première page : il suffit qu'il reste un appel. Pages suivantes : seulement si
        # elles ne mangent pas les appels réservés aux passages restants du mois.
        needed = 1 if offset == 0 else 1 + calls_reserved(now)
        if used + needed > MONTHLY_QUOTA:
            print(f"Quota : {used}/{MONTHLY_QUOTA} appels utilisés ce mois, collecte "
                  f"{'annulée' if offset == 0 else 'limitée à la première page'}.")
            break
        query = urllib.parse.urlencode({"access_key": key, "dep_iata": "BOD", "limit": 100, "offset": offset})
        try:
            body = get_json(f"{API_URL}?{query}")
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}")
        used += 1
        usage[month] = used
        save_usage(usage)
        if "error" in body:  # ne jamais afficher l'URL : elle contient la clé
            sys.exit(f"Erreur AviationStack : {body['error'].get('code')} {body['error'].get('message', '')}")
        records += body.get("data", [])
        page = body.get("pagination", {})
        offset += page.get("count", 0)
        if not page.get("count") or offset >= page.get("total", 0):
            break
    print(f"AviationStack : {len(records)} vols reçus, {used}/{MONTHLY_QUOTA} appels ce mois.")
    return records


def normalize(records, collected_at):
    """Réponse AviationStack -> lignes au format de flights.csv (heures locales)."""
    rows, codeshares = [], {}
    for r in records:
        dep, arr = r.get("departure") or {}, r.get("arrival") or {}
        flight, airline = r.get("flight") or {}, r.get("airline") or {}
        aircraft = r.get("aircraft") or {}
        if dep.get("iata") != "BOD" or not dep.get("scheduled"):
            continue
        shared = flight.get("codeshared")
        if shared:  # numéro commercial d'un vol opéré par une autre compagnie
            codeshares.setdefault(shared.get("flight_iata", "").upper(), []).append(flight.get("iata") or "")
            continue
        std, atd = dep["scheduled"][11:16], (dep.get("actual") or "")[11:16]
        rows.append({
            "date": dep["scheduled"][:10],
            "flight_iata": flight.get("iata") or "", "flight_icao": flight.get("icao") or "",
            "airline": airline.get("name") or "",
            "destination": arr.get("airport") or "", "destination_iata": arr.get("iata") or "",
            "std": hhmm(std), "atd": hhmm(atd), "status": r.get("flight_status") or "",
            "aircraft_type": aircraft.get("iata") or "", "aircraft_reg": aircraft.get("registration") or "",
            "codeshares": "", "collected_at": collected_at,
        })
    for row in rows:
        row["codeshares"] = "|".join(sorted(filter(None, codeshares.get(row["flight_iata"], []))))
    return rows


def flight_key(r):
    ident = r["flight_iata"] or r["flight_icao"] or f"{r['airline']}|{r['std']}|{r['destination_iata']}"
    return (r["date"], ident)


def merge(flights, new_rows):
    """Met à jour les vols connus (sans effacer une valeur par du vide) et ajoute les nouveaux."""
    index = {flight_key(f): f for f in flights}
    added = 0
    for r in new_rows:
        f = index.get(flight_key(r))
        if f is None:
            f = {k: "" for k in FLIGHT_FIELDS}
            f.update(first_collected_at=r["collected_at"], n_snapshots=0)
            flights.append(f)
            index[flight_key(r)] = f
            added += 1
        for k, v in r.items():
            if k != "collected_at" and v != "":
                f[k] = v
        f["n_snapshots"] = int(f["n_snapshots"] or 0) + 1
        f["last_collected_at"] = r["collected_at"]
    print(f"Fusion : {len(new_rows)} vols reçus, dont {added} nouveaux.")


# ── 2. Enrichissement ──────────────────────────────────

def easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def public_holidays(year):
    e = easter(year)
    fixed = [(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)]
    return {date(year, m, d) for m, d in fixed} | {e + timedelta(days=n) for n in (1, 39, 50)}


def school_holidays():
    """Périodes de vacances zone A (Bordeaux), mises en cache et rafraîchies tous les 30 jours."""
    cache = json.loads(HOLIDAYS_JSON.read_text()) if HOLIDAYS_JSON.exists() else None
    if not cache or date.fromisoformat(cache["fetched"]) < date.today() - timedelta(days=30):
        url = ("https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-calendrier-scolaire/records"
               "?select=description,start_date,end_date&where=location%3D%22Bordeaux%22%20and%20end_date%3E%3D%222026-01-01%22"
               "&order_by=start_date&limit=100")
        try:
            periods = []
            for r in get_json(url)["results"]:
                if r["description"].startswith("Pont"):
                    continue  # le collecteur d'origine ne comptait pas les ponts
                start = datetime.fromisoformat(r["start_date"]).astimezone(PARIS).date()
                end = datetime.fromisoformat(r["end_date"]).astimezone(PARIS).date()
                periods.append([start.isoformat(), end.isoformat()])
            cache = {"fetched": date.today().isoformat(), "periods": sorted(set(map(tuple, periods)))}
            HOLIDAYS_JSON.parent.mkdir(parents=True, exist_ok=True)
            HOLIDAYS_JSON.write_text(json.dumps(cache, indent=1) + "\n")
        except (urllib.error.URLError, KeyError, ValueError) as e:
            print(f"Calendrier scolaire indisponible ({e}), cache utilisé.")
            if not cache:
                return []
    return [(date.fromisoformat(s), date.fromisoformat(e)) for s, e in cache["periods"]]


def holiday_status(d, periods):
    if d in public_holidays(d.year):
        return "Jour férié"
    # La date de fin publiée est le jour de la reprise.
    return "Vacances" if any(s <= d < e for s, e in periods) else "Non"


def forecast_weather():
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={LAT}&longitude={LON}&past_days=2&forecast_days=3&timezone=Europe%2FParis"
           "&hourly=temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m,visibility,weather_code")
    h = get_json(url)["hourly"]
    return {t[:13]: {k: h[k][i] for k in h if k != "time"} for i, t in enumerate(h["time"])}


def enrich(flights, now):
    """Complète météo, vacances, jour, Schengen pour les vols récents ou à venir."""
    today = now.date()
    recent = [f for f in flights if f["date"] >= (today - timedelta(days=2)).isoformat()]
    if not recent:
        return
    weather = forecast_weather()
    periods = school_holidays()
    schengen = {f["destination_iata"]: f["non_schengen"] for f in flights if f["non_schengen"]}
    for f in recent:
        d = date.fromisoformat(f["date"])
        w = weather.get(f"{f['date']}T{f['std'][:2]}")
        # Le vol est déjà parti : on garde la météo connue au moment du départ.
        if w and w["temperature_2m"] is not None and not (f["atd"] and f["weather_temp"] != ""):
            code = w["weather_code"]
            f.update(weather_temp=w["temperature_2m"], weather_precip=w["precipitation"],
                     weather_wind=w["wind_speed_10m"], weather_gusts=w["wind_gusts_10m"],
                     weather_visibility=w["visibility"], weather_code=code, weather_label=weather_label(code))
        f["school_holiday"] = holiday_status(d, periods)
        f["day_of_week"] = JOURS[d.weekday()]
        f["non_schengen"] = schengen.get(f["destination_iata"], "Non")
        f["is_commercial"] = int(bool(f["flight_iata"]))
        if f["atd"]:
            f["delay_min"] = delay_between(f["std"], f["atd"])
            f["delay_flag"] = delay_flag(f["delay_min"])


# ── 3. Prédiction ──────────────────────────────────────

def rounded(v):
    return None if v is None else round(v, 1)


def risk(p):
    return next((lvl, text) for limit, lvl, text in RISK_LEVELS if p < limit)


def prediction_type(f, now):
    dep = datetime.strptime(f"{f['date']} {f['std']}", "%Y-%m-%d %H:%M").replace(tzinfo=PARIS)
    if dep.date() > now.date():
        return "J-1"
    return "T-2h" if dep - now <= timedelta(hours=2, minutes=30) else "Jour J"


def predict(flights, now):
    today, tomorrow = now.date().isoformat(), (now.date() + timedelta(days=1)).isoformat()
    featured = model.build_features(flights)
    clf = model.train(featured)
    metrics = model.evaluate(featured)
    (DATA / "model_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")

    log = read_csv(PREDICTIONS_LOG) if PREDICTIONS_LOG.exists() else []
    last_logged = {}
    for r in log:
        last_logged[(r["date"], r["flight_iata"])] = r

    stamp = now.strftime("%Y-%m-%d %H:%M")
    targets = [f for f in featured if f["date"] in (today, tomorrow)]
    out = []
    for f in targets:
        dep = datetime.strptime(f"{f['date']} {f['std']}", "%Y-%m-%d %H:%M").replace(tzinfo=PARIS)
        key = (f["date"], f["flight_iata"])
        if dep > now and not f["atd"]:  # on ne prédit que des vols pas encore partis
            p = float(clf.predict_proba([f["_x"]])[0, 1])
            lvl, _ = risk(p)
            entry = {"date": f["date"], "flight_iata": f["flight_iata"], "std": f["std"], "predicted_at": stamp,
                     "prediction_type": prediction_type(f, now), "probabilite_retard": round(100 * p, 1),
                     "risk_level": lvl}
            log.append(entry)
            last_logged[key] = entry
        pred = last_logged.get(key)
        if not pred:
            continue  # vol déjà parti avant notre première prédiction
        p = float(pred["probabilite_retard"]) / 100
        lvl, text = risk(p)
        out.append({
            "date": f["date"], "flight_iata": f["flight_iata"], "airline": f["airline"],
            "destination": f["destination"], "destination_iata": f["destination_iata"], "std": f["std"],
            "status": f["status"], "atd": f["atd"], "delay_min": f["delay_min"],
            "weather_label": f["weather_label"], "weather_temp": f["weather_temp"],
            "weather_wind": f["weather_wind"], "weather_precip": f["weather_precip"],
            "avg_delay_flight": rounded(f["_avg_delay_flight"]), "avg_delay_airline": rounded(f["_avg_delay_airline"]),
            "probabilite_retard": round(100 * p, 1), "risk_level": lvl, "label_retard": text,
            "prediction_type": pred["prediction_type"], "predicted_at": pred["predicted_at"],
        })
    out.sort(key=lambda r: (r["date"], r["std"], r["flight_iata"]))
    write_csv(PREDICTIONS_LOG, log, LOG_FIELDS)
    PREDICTIONS_JSON.write_text(json.dumps({
        "generated_at": stamp,
        "model": {"auc": metrics["auc"], "evaluated_on": f"{metrics['test_from']} → {metrics['test_to']}",
                  "threshold_min": model.DELAY_THRESHOLD},
        "flights": out,
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"Prédictions : {len(out)} vols ({today} et {tomorrow}), AUC test {metrics['auc']}.")


# ── Main ───────────────────────────────────────────────

def main():
    now = now_paris()
    flights = read_csv(FLIGHTS_CSV)
    if "--no-collect" not in sys.argv:
        if "--from-file" in sys.argv:
            records = json.loads(open(sys.argv[sys.argv.index("--from-file") + 1]).read())["data"]
        else:
            records = fetch_aviationstack(now)
        merge(flights, normalize(records, now.strftime("%Y-%m-%d %H:%M:%S")))
    enrich(flights, now)
    mark_primary(flights)
    flights.sort(key=lambda f: (f["date"], f["std"], f["flight_iata"] or f["flight_icao"]))
    write_csv(FLIGHTS_CSV, flights, FLIGHT_FIELDS)
    predict(flights, now)


if __name__ == "__main__":
    main()
