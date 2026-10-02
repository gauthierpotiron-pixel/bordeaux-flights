# Rapport de nettoyage

Source : export du Google Sheets du 2026-10-02T18:37:00Z. Généré par `scripts/clean_history.py`.

## Départs (`flights.csv`)

| | Nombre |
|---|---|
| Lignes brutes | 17067 |
| Vols après fusion des doublons | 12147 |
| Lignes fusionnées | 4920 |
| Vols commerciaux (numéro IATA) | 11021 |
| Départs physiques commerciaux (sans doublon de partage de code) | 10829 |
| dont avec retard réel exploitable | 7920 (73 %) |
| Annulés | 115 |
| Retards signalés aberrants (hors -30 à +600 min) | 58 |
| Avec météo | 12147 (100 %) |
| Avec immatriculation | 524 (4 %) |

Période : du 2026-04-05 au 2026-10-02, 165 jours collectés.

Jours sans aucune collecte (16) : 2026-04-06, 2026-05-02, 2026-06-08, 2026-06-09, 2026-06-10, 2026-06-21, 2026-06-22, 2026-06-23, 2026-06-24, 2026-06-25, 2026-06-26, 2026-06-27, 2026-06-28, 2026-06-29, 2026-06-30, 2026-07-01. Ils ne sont pas récupérables avec le plan gratuit AviationStack.

Retard à 15 min ou plus, par mois (départs commerciaux principaux avec retard réel) :

| Mois | Vols | Part en retard ≥ 15 min |
|---|---|---|
| 2026-04 | 1226 | 36 % |
| 2026-05 | 1588 | 36 % |
| 2026-06 | 767 | 40 % |
| 2026-07 | 1487 | 55 % |
| 2026-08 | 1417 | 48 % |
| 2026-09 | 1397 | 37 % |
| 2026-10 | 38 | 47 % |

## Arrivées (`arrivals.csv`)

2961 lignes brutes, 765 vols après fusion, 229 avec heure d'arrivée réelle. Jours couverts : 2026-05-16 (173), 2026-05-17 (207), 2026-05-18 (179), 2026-05-19 (151), 2026-10-02 (55).
