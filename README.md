# brief-energie-data

Collecte quotidienne des données électricité France pour le *Brief Marchés Énergie*.

- `collect.py` interroge l'API ENTSO-E Transparency Platform et l'API RTE Data, puis écrit :
  - `data/brief.json` : prix day-ahead horaires, production réalisée par filière, consommation,
    prévisions (consommation, éolien, solaire, disponibilité nucléaire) et indisponibilités nucléaires ;
  - `data/status.json` : journal de la dernière collecte.
- `.github/workflows/collect.yml` lance la collecte tous les jours à 5 h 35 et 12 h 35 UTC, et à la demande.

Secrets attendus (Settings → Secrets and variables → Actions) : `ENTSOE_TOKEN`, `RTE_CLIENT_ID`, `RTE_CLIENT_SECRET`.
Ils ne sont jamais écrits dans les fichiers ni dans les journaux.
