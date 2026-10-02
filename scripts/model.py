"""Modèle de prédiction : probabilité qu'un départ de BOD parte avec 15 min de retard ou plus.

Les statistiques historiques (retard moyen du vol, de la compagnie, tendance récente à BOD)
sont calculées uniquement avec les jours qui précèdent le vol : le modèle n'utilise jamais
une information qu'il n'aurait pas eue au moment de prédire.

Usage : python3 scripts/model.py   (évalue le modèle et écrit data/model_metrics.json)
"""
import json
import math
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

from common import CLEAN, DATA, minutes, read_csv

DELAY_THRESHOLD = 15   # minutes
SHRINK = 10            # poids de la moyenne globale pour les vols peu observés
TEST_DAYS = 28         # l'évaluation porte sur les 4 dernières semaines

FEATURES = [
    "hour", "day_of_week", "holiday", "non_schengen",
    "weather_temp", "weather_precip", "weather_wind", "weather_gusts", "weather_visibility", "weather_code",
    "flight_rate", "flight_avg_delay", "flight_count", "airline_rate", "airline_avg_delay",
    "destination_rate", "bod_rate_7d", "airline_rate_14d",
]
HOLIDAY = {"Non": 0, "Vacances": 1, "Jour férié": 2}


def num(v, default=0.0):
    return float(v) if v not in ("", None) else default


def is_usable(f):
    """Départ commercial, un seul numéro par avion, pas annulé, retard réel plausible."""
    return f["is_commercial"] in ("1", 1) and f["is_primary"] in ("1", 1) and f["status"] != "cancelled"


def label(f):
    if f["delay_min"] == "" or f.get("delay_flag"):
        return None
    return int(float(f["delay_min"]) >= DELAY_THRESHOLD)


class Stats:
    """Compteurs cumulés : nombre de vols, nombre en retard, somme des retards."""

    def __init__(self):
        self.n = defaultdict(int)
        self.late = defaultdict(int)
        self.delay = defaultdict(float)

    def add(self, key, late, delay):
        self.n[key] += 1
        self.late[key] += late
        self.delay[key] += delay

    def rate(self, key, prior):
        return (self.late[key] + SHRINK * prior) / (self.n[key] + SHRINK)

    def avg(self, key, prior):
        return (self.delay[key] + SHRINK * prior) / (self.n[key] + SHRINK)

    def raw_avg(self, key):
        return self.delay[key] / self.n[key] if self.n[key] else None


def build_features(flights):
    """Ajoute à chaque vol ses variables, calculées avec les jours précédents uniquement."""
    flights = sorted((f for f in flights if is_usable(f)), key=lambda f: (f["date"], f["std"]))
    by_date = defaultdict(list)
    for f in flights:
        by_date[f["date"]].append(f)

    st_flight, st_airline, st_dest = Stats(), Stats(), Stats()
    total_n = total_late = 0
    total_delay = 0.0
    recent = deque()  # (date, airline, late) sur les 14 derniers jours

    for d in sorted(by_date):
        day = date.fromisoformat(d)
        while recent and recent[0][0] < day - timedelta(days=14):
            recent.popleft()
        last7 = [r for r in recent if r[0] >= day - timedelta(days=7)]
        prior_rate = total_late / total_n if total_n else 0.4
        prior_delay = total_delay / total_n if total_n else 15.0
        bod7 = (sum(r[2] for r in last7) + SHRINK * prior_rate) / (len(last7) + SHRINK)
        air14 = defaultdict(lambda: [0, 0])
        for r in recent:
            air14[r[1]][0] += 1
            air14[r[1]][1] += r[2]

        for f in by_date[d]:
            fl, al, de = f["flight_iata"], f["airline"], f["destination_iata"]
            n14, late14 = air14[al]
            f["_x"] = [
                minutes(f["std"]) / 60,
                day.weekday(),
                HOLIDAY.get(f["school_holiday"], 0),
                int(f["non_schengen"] == "Oui"),
                num(f["weather_temp"], 15), num(f["weather_precip"]), num(f["weather_wind"]),
                num(f["weather_gusts"]), num(f["weather_visibility"], 30000), num(f["weather_code"]),
                st_flight.rate(fl, prior_rate), st_flight.avg(fl, prior_delay), math.log1p(st_flight.n[fl]),
                st_airline.rate(al, prior_rate), st_airline.avg(al, prior_delay),
                st_dest.rate(de, prior_rate),
                bod7,
                (late14 + SHRINK * st_airline.rate(al, prior_rate)) / (n14 + SHRINK),
            ]
            f["_avg_delay_flight"] = st_flight.raw_avg(fl)
            f["_avg_delay_airline"] = st_airline.raw_avg(al)

        # Les résultats du jour ne servent qu'aux jours suivants.
        for f in by_date[d]:
            y = label(f)
            if y is None:
                continue
            delay = float(f["delay_min"])
            for st, key in ((st_flight, f["flight_iata"]), (st_airline, f["airline"]), (st_dest, f["destination_iata"])):
                st.add(key, y, delay)
            total_n, total_late, total_delay = total_n + 1, total_late + y, total_delay + delay
            recent.append((day, f["airline"], y))
    return flights


def new_model():
    return RandomForestClassifier(n_estimators=300, min_samples_leaf=20, max_features=0.5,
                                  n_jobs=-1, random_state=42)


def train(flights):
    labelled = [f for f in flights if label(f) is not None]
    model = new_model()
    model.fit([f["_x"] for f in labelled], [label(f) for f in labelled])
    return model


def evaluate(flights):
    """Entraîne sur tout sauf les 4 dernières semaines, mesure sur ces 4 semaines."""
    labelled = [f for f in flights if label(f) is not None]
    last = max(f["date"] for f in labelled)
    cut = (date.fromisoformat(last) - timedelta(days=TEST_DAYS)).isoformat()
    tr = [f for f in labelled if f["date"] <= cut]
    te = [f for f in labelled if f["date"] > cut]
    model = new_model().fit([f["_x"] for f in tr], [label(f) for f in tr])
    p = model.predict_proba([f["_x"] for f in te])[:, 1]
    y = [label(f) for f in te]
    base = [f["_x"][FEATURES.index("airline_rate")] for f in te]
    importance = sorted(zip(FEATURES, model.feature_importances_), key=lambda t: -t[1])
    return {
        "train_rows": len(tr), "test_rows": len(te), "test_from": cut, "test_to": last,
        "late_share_test": round(sum(y) / len(y), 3),
        "auc": round(roc_auc_score(y, p), 3),
        "auc_airline_only": round(roc_auc_score(y, base), 3),
        "brier": round(brier_score_loss(y, p), 3),
        "importance": {k: round(v, 3) for k, v in importance},
    }


if __name__ == "__main__":
    flights = build_features(read_csv(CLEAN / "flights.csv"))
    metrics = evaluate(flights)
    (DATA / "model_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
