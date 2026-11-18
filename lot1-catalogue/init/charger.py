"""
Lot 1 — Chargement initial de MongoDB
Exécuté par le conteneur ponctuel « mongo-init » à chaque « docker compose up ».

Comme l'ancien dossier /docker-entrypoint-initdb.d, il ne charge que si la base est
vide : les requêtes types de démonstration (03-evolutions-schema.py) laissent leurs
évolutions en place, un rechargement les effacerait sans prévenir.
  Repartir de zéro :    docker compose down -v
  Forcer le rechargement : docker compose run --rm -e FORCER=1 mongo-init
"""
import os
import runpy
import sys
import time

sys.path.insert(0, "/lot1/lib")
from pymongo.errors import ServerSelectionTimeoutError  # noqa: E402

from outils import connecter  # noqa: E402

db = connecter()
for essai in range(30):                     # le service « mongodb » répond en général tout de suite
    try:
        db.command("ping")
        break
    except ServerSelectionTimeoutError:
        time.sleep(2)

deja = "products" in db.list_collection_names() and "ux_slug" in [i["name"] for i in db.products.list_indexes()]
if deja and os.environ.get("FORCER") != "1":
    print(f"[lot1] Catalogue déjà chargé ({db.products.estimated_document_count()} produits) : rien à faire.")
    print("       Pour repartir de zéro : docker compose down -v ; docker compose up -d")
    sys.exit(0)

debut = time.time()
for script in ["01-schema.py", "02-import-catalogue.py", "03-avis-synthetiques.py",
               "04-compatibilites.py", "05-index.py"]:
    runpy.run_path(f"/lot1/init/{script}", run_name="__main__")
print(f"[lot1] OK en {time.time() - debut:.0f} s")
