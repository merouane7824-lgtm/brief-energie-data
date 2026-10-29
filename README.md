# Collecte de données électricité France (ENTSO-E, RTE, OMIP)

`collect.py` (lancé par `.github/workflows/collect.yml`) écrit `data/brief.json` et `data/forwards.json`, lus chaque matin par la tâche qui prépare le Brief Marchés Énergie.

Le brief lui-même est publié sur un site à accès restreint ; `index.html` et `docs/index.html` ne font que rediriger vers lui.
