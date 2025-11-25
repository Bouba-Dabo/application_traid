# Application Traid — Analyseur boursier (yfinance)

Ceci est une application de démonstration qui récupère des données via `yfinance`, calcule des indicateurs techniques, exécute un moteur de règles (DSL) et affiche les résultats via une interface Streamlit.

Résumé des composants:
- Backend: collecte via `yfinance`, calcul d'indicateurs (`pandas_ta`).
- Moteur DSL: règles simples lisibles par l'utilisateur (`rules.dsl`).
- UI: `Streamlit` avec champ de recherche, affichage des indicateurs et historique.
- Persistance: SQLite (`analyses.db`).
- VM: `Vagrantfile` fourni pour démarrer une VM Ubuntu et exécuter l'application.

Installation locale
1. Créez un environnement virtuel et installez les dépendances:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
2. Lancer l'app:
```powershell
streamlit run app/main.py
```

Exécution dans une VM (Vagrant)
1. Installez Vagrant et VirtualBox.
2. Depuis ce dossier:
```powershell
vagrant up
vagrant ssh
# puis à l'intérieur de la VM:
cd /vagrant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/main.py --server.address=0.0.0.0
```

Notes
- Les données en "temps réel" viennent de `yfinance` et peuvent avoir une latence.
- Modifiez `rules.dsl` pour ajouter/ajuster des règles.