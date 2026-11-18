"""
Lot 1 — Requêtes types : LECTURES (le catalogue est lu beaucoup plus qu'écrit)

Lancement : docker compose run --rm mongo-init python /lot1/queries/01-lectures.py
Ce script ne modifie aucune donnée.
"""
import sys

sys.path.insert(0, "/lot1/lib")
from bson import Decimal128  # noqa: E402

from outils import afficher, arrondi, connecter, decimales, plan, tableau, texte, titre  # noqa: E402

db = connecter()


def prix(p):
    return f"{p['price']['amount']} {p['price']['currency']}" if p.get("price") else "-"


def note(p):
    r = p.get("rating") or {}
    return f"{texte(r.get('average'))} ({r['count']} avis)" if r.get("count") else "-"


def court(s, n=42):
    return s[:n - 1] + "…" if len(s) > n else s


def ligne(p):
    return {"sku": p["_id"], "produit": court(p["name"]), "marque": (p.get("brand") or {}).get("name"),
            "prix": prix(p), "note": note(p)}


COLONNES = ["sku", "produit", "marque", "prix", "note"]
PROJ_LISTING = {"name": 1, "brand.name": 1, "price": 1, "rating.average": 1, "rating.count": 1}
PAR_PRIX = [("price.amount", 1), ("_id", 1)]

# -----------------------------------------------------------------------------------------
titre("R1 - Page produit : UNE seule lecture renvoie tout ce que la page affiche",
      "Fiche + caractéristiques + note + 5 derniers avis + produits compatibles. Index unique ux_slug.")
SLUG = "msi-pro-b760-p-wifi-ddr4-mb-000131"
fiche = db.products.find_one({"slug": SLUG}, {"source": 0})
afficher({
    "_id": fiche["_id"], "name": fiche["name"], "brand": fiche["brand"], "category": fiche["category"],
    "status": fiche["status"], "price": prix(fiche), "specs": fiche["specs"], "rating": fiche["rating"],
    "recentReviews": f"{len(fiche['recentReviews'])} avis embarqués sur {fiche['rating']['count']} (Subset pattern)",
    "dernierAvis": fiche["recentReviews"][0],
    "compatibleProducts": [f"{c['_id']}  {c['name']}  [{c['reason']}]" for c in fiche["compatibleProducts"]],
})
plan(db.products.find({"slug": SLUG}).explain())

print("\n   Libellés : lus dans le gabarit de la catégorie (32 documents, gardés en cache par l'application)")
gabarit = db.categories.find_one({"_id": fiche["category"]["id"]}, {"specTemplate": 1})["specTemplate"]
libelles = {s["k"]: s["label"] for s in gabarit}
for s in fiche["specs"]:
    print(f"     {libelles[s['k']]} : {texte(s['v'])}{' ' + s['u'] if s.get('u') else ''}")

# -----------------------------------------------------------------------------------------
titre("R2 - Listing d'une catégorie ET de ses sous-catégories, trié par prix, paginé",
      "\"Refroidissement\" contient 4 sous-catégories : une seule égalité sur category.path (Tree pattern).")
filtre_listing = {"category.path": "refroidissement", "status": "active"}
page1 = list(db.products.find(filtre_listing, PROJ_LISTING).sort(PAR_PRIX).limit(5))
tableau([ligne(p) for p in page1], COLONNES)
plan(db.products.find(filtre_listing, PROJ_LISTING).sort(PAR_PRIX).limit(5).explain())

print("\n   Page 2 par curseur (keyset) : on repart du dernier élément au lieu de faire skip(),")
print("   dont le coût augmente avec le numéro de page.")
dernier = page1[-1]
filtre_page2 = {
    **filtre_listing,
    "$or": [
        {"price.amount": {"$gt": dernier["price"]["amount"]}},
        {"price.amount": dernier["price"]["amount"], "_id": {"$gt": dernier["_id"]}},
    ],
}
tableau([ligne(p) for p in db.products.find(filtre_page2, PROJ_LISTING).sort(PAR_PRIX).limit(5)], COLONNES)

# -----------------------------------------------------------------------------------------
titre("R3 - Filtres à facettes sur des attributs variables (Attribute pattern)",
      "Processeurs socket AM5, au moins 8 cœurs, TDP <= 120 W, prix <= 500 USD. Index ix_facettes.")
filtre_facettes = {
    "category.id": "processeurs",
    "status": "active",
    "specs": {
        "$all": [
            {"$elemMatch": {"k": "socket", "v": "AM5"}},
            {"$elemMatch": {"k": "core_count", "v": {"$gte": 8}}},
            {"$elemMatch": {"k": "tdp", "v": {"$lte": 120}}},
        ],
    },
    "price.amount": {"$lte": Decimal128("500")},
}
tableau([ligne(p) for p in db.products.find(filtre_facettes, PROJ_LISTING).sort("price.amount", 1)], COLONNES)
plan(db.products.find(filtre_facettes).explain())

# -----------------------------------------------------------------------------------------
titre("R4 - Calcul des facettes d'une catégorie (nombre de produits par valeur)",
      "Les facettes proposées viennent du gabarit de la catégorie (filterable: true).")
facettes_ram = [s for s in db.categories.find_one({"_id": "memoire-ram"})["specTemplate"] if s["filterable"]]
lib_facette = {s["k"]: s["label"] + (f" ({s['unit']})" if s.get("unit") else "") for s in facettes_ram}
for f in db.products.aggregate([
    {"$match": {"category.id": "memoire-ram", "status": "active"}},
    {"$unwind": "$specs"},
    {"$match": {"specs.k": {"$in": ["ddr_generation", "capacity_gb", "speed_mhz", "module_count"]}}},
    {"$group": {"_id": {"k": "$specs.k", "v": "$specs.v"}, "n": {"$sum": 1}}},
    {"$sort": {"n": -1}},
    {"$group": {"_id": "$_id.k", "valeurs": {"$push": {"v": "$_id.v", "n": "$n"}}}},
    {"$project": {"valeurs": {"$slice": ["$valeurs", 6]}}},
    {"$sort": {"_id": 1}},
]):
    valeurs = " · ".join("{} ({})".format(texte(x["v"]), x["n"]) for x in f["valeurs"])
    print(f"   {lib_facette[f['_id']]:<26} {valeurs}")

# -----------------------------------------------------------------------------------------
titre("R5 - Avis d'un best-seller au-delà des 5 embarqués (collection reviews)",
      "Page 2 des avis du produit le plus commenté. Index ix_avis_produit.")
filtre_avis = {"productId": "COOL-000095", "status": "approved"}
tableau([{"date": a["createdAt"], "note": a["rating"], "titre": a["title"], "client": a["customer"]["displayName"]}
         for a in db.reviews.find(filtre_avis, {"rating": 1, "title": 1, "customer.displayName": 1, "createdAt": 1})
         .sort("createdAt", -1).skip(5).limit(5)],
        ["date", "note", "titre", "client"])
plan(db.reviews.find(filtre_avis).sort("createdAt", -1).skip(5).limit(5).explain())

# -----------------------------------------------------------------------------------------
titre("R6 - Recherche plein texte", "Expression exacte \"ryzen 7\", produits en vente, triés par pertinence. Index tx_recherche.")
tableau([ligne(p) for p in db.products.find(
    {"$text": {"$search": "\"ryzen 7\""}, "status": "active"},
    {"score": {"$meta": "textScore"}, **PROJ_LISTING},
).sort([("score", {"$meta": "textScore"}), ("price.amount", 1)]).limit(5)], COLONNES)

# -----------------------------------------------------------------------------------------
titre("R7 - Produits d'une marque, avec des critères techniques",
      "Kits Corsair DDR5 de 32 Go, du moins cher au plus cher.")
filtre_marque = {
    "brand.id": "corsair", "category.id": "memoire-ram", "status": "active",
    "specs": {"$all": [{"$elemMatch": {"k": "ddr_generation", "v": "DDR5"}},
                       {"$elemMatch": {"k": "capacity_gb", "v": 32}}]},
}
tableau([ligne(p) for p in db.products.find(filtre_marque, PROJ_LISTING).sort("price.amount", 1).limit(5)], COLONNES)
plan(db.products.find(filtre_marque).sort("price.amount", 1).limit(5).explain())

# -----------------------------------------------------------------------------------------
titre("R8 - Les mieux notés d'une catégorie (au moins 20 avis)",
      "La note est un champ calculé (Computed pattern) : on trie dessus sans relire les avis. Index ix_listing_note.")
filtre_note = {"category.path": "ecrans", "status": "active", "rating.count": {"$gte": 20}}
PAR_NOTE = [("rating.average", -1), ("_id", 1)]
tableau([ligne(p) for p in db.products.find(filtre_note, PROJ_LISTING).sort(PAR_NOTE).limit(5)], COLONNES)
plan(db.products.find(filtre_note).sort(PAR_NOTE).limit(5).explain())

# -----------------------------------------------------------------------------------------
titre("R9 - Back-office : indicateurs par famille de produits (pipeline d'agrégation)")
tableau([{
    "famille": f["_id"], "produits": f["produits"], "prix moyen": decimales(float(str(f["prixMoyen"])), 2),
    "prix max": str(f["prixMax"]), "note moyenne": decimales(f["noteMoyenne"], 2) if f["noteMoyenne"] else "-",
    "avis": f["avis"],
} for f in db.products.aggregate([
    {"$match": {"status": "active"}},
    {"$group": {
        "_id": {"$first": "$category.path"},
        "produits": {"$sum": 1},
        "prixMoyen": {"$avg": "$price.amount"},
        "prixMax": {"$max": "$price.amount"},
        "noteMoyenne": {"$avg": "$rating.average"},
        "avis": {"$sum": "$rating.count"},
    }},
    {"$sort": {"produits": -1}},
])], ["famille", "produits", "prix moyen", "prix max", "note moyenne", "avis"])

# -----------------------------------------------------------------------------------------
titre("R10 - Back-office : file de modération des avis, avec le nom du produit ($lookup)",
      "$lookup est coûteux : réservé à un écran interne peu sollicité, jamais à la page produit.")
tableau([{"déposé le": a["createdAt"], "note": a["rating"], "titre": a["title"], "produit": court(a["produit"], 38)}
         for a in db.reviews.aggregate([
             {"$match": {"status": "pending"}},
             {"$sort": {"createdAt": 1}},
             {"$limit": 5},
             {"$lookup": {"from": "products", "localField": "productId", "foreignField": "_id", "as": "produit",
                          "pipeline": [{"$project": {"name": 1}}]}},
             {"$project": {"createdAt": 1, "rating": 1, "title": 1, "produit": {"$first": "$produit.name"}}},
         ])], ["déposé le", "note", "titre", "produit"])

# -----------------------------------------------------------------------------------------
titre("R11 - Justification chiffrée : pourquoi les avis ne sont pas tous embarqués")
taille_fiche = next(db.products.aggregate([{"$match": {"_id": "COOL-000095"}},
                                           {"$project": {"o": {"$bsonSize": "$$ROOT"}}}]))["o"]
tous_avis = next(db.reviews.aggregate([
    {"$match": {"productId": "COOL-000095"}},
    {"$group": {"_id": None, "n": {"$sum": 1}, "o": {"$sum": {"$bsonSize": "$$ROOT"}}}},
]))
stats = next(db.products.aggregate([
    {"$project": {"o": {"$bsonSize": "$$ROOT"}}},
    {"$group": {"_id": None, "moyenne": {"$avg": "$o"}, "max": {"$max": "$o"}}},
]))
print(f"   Fiche du best-seller COOL-000095 (5 avis embarqués) : {decimales(taille_fiche / 1024, 1)} Ko")
print(f"   Ses {tous_avis['n']} avis, s'ils étaient embarqués        : {decimales(tous_avis['o'] / 1024, 1)} Ko")
print(f"   -> chaque affichage de la page lirait {arrondi(tous_avis['o'] / taille_fiche)} fois plus de données")
print(f"   Taille des fiches produit : moyenne {arrondi(stats['moyenne'])} octets, "
      f"maximum {decimales(stats['max'] / 1024, 1)} Ko (limite MongoDB : 16 Mo)")
