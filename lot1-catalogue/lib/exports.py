"""
Lot 1 — Exports du catalogue vers les autres lots (fichiers versionnés dans leur data/)

  skus        SKU en vente, un par ligne               -> lot2-panier-sessions/data/skus-vendables.txt
  limites     limites d'achat lors des sorties         -> lot2-panier-sessions/data/limites-achat.txt
  produits    produits en vente, une ligne TSV chacun  -> lot3-logs-analytics/data/produits.tsv
              (avec leurs attributs de compatibilité)     lot4-recommandations/data/produits.tsv
  categories  arbre des catégories                     -> lot4-recommandations/data/categories.tsv

Les exports partent toujours du catalogue À L'ÉTAT INITIAL, sans rien demander : le catalogue
est chargé dans une base temporaire par les mêmes scripts qu'au premier démarrage (01 à 03),
exporté, puis supprimé. Les écritures de démonstration faites dans ldlc_catalogue (scripts
02, 03, 04) ne sont ni touchées ni exportées.

Depuis la racine du dépôt, une seule commande régénère les cinq fichiers :
  docker compose run --rm mongo-init python /lot1/lib/exports.py
Un seul export, sur la sortie standard :
  docker compose run --rm -T mongo-init python /lot1/lib/exports.py skus
"""
import os
import re
import runpy
import sys
from contextlib import redirect_stdout

sys.path.insert(0, "/lot1/lib")
from outils import connecter, texte  # noqa: E402

BASE_TEMPORAIRE = "ldlc_catalogue_export"
# Chemin dans le conteneur -> chemin dans le dépôt (volumes de mongo-init)
FICHIERS = {
    "skus": {"/export/lot2/skus-vendables.txt": "lot2-panier-sessions/data/skus-vendables.txt"},
    "limites": {"/export/lot2/limites-achat.txt": "lot2-panier-sessions/data/limites-achat.txt"},
    "produits": {"/export/lot3/produits.tsv": "lot3-logs-analytics/data/produits.tsv",
                 "/export/lot4/produits.tsv": "lot4-recommandations/data/produits.tsv"},
    "categories": {"/export/lot4/categories.tsv": "lot4-recommandations/data/categories.tsv"},
}
EN_VENTE = {"status": "active"}


def net(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def skus(db):
    for p in db.products.find(EN_VENTE, {"_id": 1}).sort("_id", 1):
        yield p["_id"]


def limites(db):
    """Produits limités à 2 par client : RTX 5090, 5080, 5070 Ti et Ryzen 7 9800X3D."""
    yield "# Limites d'achat par client lors des sorties de composants : SKU quantité_max"
    yield "# Les autres produits ont la limite par défaut (10), passée aux fonctions du panier."
    yield "# Généré depuis le Lot 1 : cartes RTX 5090 / 5080 / 5070 Ti et Ryzen 7 9800X3D en vente."
    puces = ["GeForce RTX 5090", "GeForce RTX 5080", "GeForce RTX 5070 Ti"]
    filtre = {**EN_VENTE, "$or": [{"_id": "CPU-000001"},
                                  {"specs": {"$elemMatch": {"k": "chipset", "v": {"$in": puces}}}}]}
    for p in db.products.find(filtre, {"_id": 1}).sort("_id", 1):
        yield f"{p['_id']} 2"


# Attributs de compatibilité, pour les paniers du Lot 3 et les interfaces du graphe (Lot 4).
# Type de mémoire d'une carte mère : absent du jeu source. Déduit du nom (« ... DDR4 », ou
# l'abréviation « D4 » / « D5 » des noms Asus et ASRock : « PRIME B760M-A D4 », « Pro RS/D4 »),
# sinon du socket, comme le socket des processeurs au Lot 1 (règle documentée, vérifiable).
# « D3 » n'est pas retenu : chez Gigabyte, c'est un nom de modèle (« GA-Z270P-D3 », en DDR4).
MEMOIRE_DANS_LE_NOM = re.compile(r"DDR([2-5])|(?<![A-Za-z0-9])D([45])(?![A-Za-z0-9])", re.IGNORECASE)
MEMOIRE_PAR_SOCKET = {
    "AM5": "DDR5", "LGA1851": "DDR5", "LGA1700": "DDR5",
    "AM4": "DDR4", "LGA1200": "DDR4", "LGA1151": "DDR4", "LGA2066": "DDR4", "sTRX4": "DDR4",
    "LGA1150": "DDR3", "LGA2011": "DDR3", "AM3+": "DDR3", "AM3": "DDR3", "AM3/AM2+/AM2": "DDR3",
    "LGA775": "DDR2",
}
# Une carte « Thin Mini ITX » entre dans un boîtier Mini ITX
FORMAT_CARTE = {"Thin Mini ITX": "Mini ITX"}


def valeurs(p, k):
    return [s["v"] for s in p.get("specs") or [] if s["k"] == k]


def valeur(p, k):
    return next(iter(valeurs(p, k)), None)


def memoire(p):
    if p["category"]["id"] == "memoire-ram":
        return valeur(p, "ddr_generation")
    if p["category"]["id"] != "cartes-meres":
        return None
    m = MEMOIRE_DANS_LE_NOM.search(p["name"])
    return "DDR" + (m.group(1) or m.group(2)) if m else MEMOIRE_PAR_SOCKET.get(valeur(p, "socket"))


def produits(db):
    yield "\t".join(["sku", "category", "brand", "price", "reviews", "name", "keywords",
                     "socket", "memory_type", "form_factor", "case_form_factors", "chipset"])
    for p in db.products.find({**EN_VENTE, "price.amount": {"$ne": None}}).sort("_id", 1):
        cat = p["category"]["id"]
        mots = " ".join(texte(s["v"]) + " " + s["u"] if s.get("u") else texte(s["v"]) for s in p.get("specs") or [])
        socket = valeur(p, "socket") if cat in ("processeurs", "cartes-meres") else None
        format_carte = valeur(p, "form_factor") if cat == "cartes-meres" else None
        yield "\t".join([p["_id"], cat, net(p["brand"].get("name") or ""), str(p["price"]["amount"]),
                         texte(p["rating"]["count"]), net(p["name"]), net(mots),
                         socket or "",
                         memoire(p) or "",
                         FORMAT_CARTE.get(format_carte, format_carte) if format_carte else "",
                         "|".join(valeurs(p, "mb_form_factor")) if cat == "boitiers" else "",
                         net(valeur(p, "chipset") or "") if cat == "cartes-graphiques" else ""])


def categories(db):
    yield "\t".join(["id", "name", "parent"])
    for c in db.categories.find().sort("_id", 1):
        yield "\t".join([c["_id"], c["name"], c.get("parent") or ""])


EXPORTS = {"skus": skus, "limites": limites, "produits": produits, "categories": categories}


def copie_initiale():
    """Charge le catalogue dans la base temporaire, comme au premier démarrage. Les messages
    de chargement partent sur la sortie d'erreur, pour ne pas se mêler aux exports."""
    os.environ["MONGO_DB"] = BASE_TEMPORAIRE
    print(f"[exports] Catalogue à l'état initial chargé dans la base temporaire {BASE_TEMPORAIRE}", file=sys.stderr)
    with redirect_stdout(sys.stderr):
        for script in ("01-schema.py", "02-import-catalogue.py", "03-avis-synthetiques.py"):
            runpy.run_path(f"/lot1/init/{script}", run_name="__main__")
    return connecter()


def main():
    demandes = sys.argv[1:] or list(EXPORTS)
    inconnus = [d for d in demandes if d not in EXPORTS]
    if inconnus:
        sys.exit(f"Export inconnu : {', '.join(inconnus)} (choix : {', '.join(EXPORTS)})")
    db = copie_initiale()
    try:
        if sys.argv[1:]:                                   # un export précis : sur la sortie standard
            for nom in demandes:
                for ligne in EXPORTS[nom](db):
                    print(ligne)
            return
        for nom in demandes:                               # sans argument : les cinq fichiers
            contenu = "".join(ligne + "\n" for ligne in EXPORTS[nom](db))
            for chemin, dans_le_depot in FICHIERS[nom].items():
                ancien = open(chemin, encoding="utf-8").read() if os.path.exists(chemin) else None
                if contenu != ancien:
                    with open(chemin, "w", encoding="utf-8", newline="\n") as f:
                        f.write(contenu)
                etat = "inchangé" if contenu == ancien else "mis à jour"
                print(f"[exports] {dans_le_depot} : {contenu.count(chr(10))} lignes, {etat}", file=sys.stderr)
    finally:
        db.client.drop_database(BASE_TEMPORAIRE)
        print(f"[exports] Base temporaire {BASE_TEMPORAIRE} supprimée", file=sys.stderr)


if __name__ == "__main__":
    main()
