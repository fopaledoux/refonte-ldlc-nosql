"""
Lot 1 — Requêtes types : ÉCRITURES (rares, mais il faut en connaître le coût)

Lancement : docker compose run --rm mongo-init python /lot1/queries/02-ecritures.py [--annuler]
  sans option : les écritures E1 à E7 sont appliquées et RESTENT dans la base (à vérifier
                dans Compass : les filtres sont affichés à la fin). Chaque exécution repart
                de l'état initial, donc le résultat est le même à chaque fois ;
  --annuler   : retire ces écritures et remet les données dans leur état initial.
"""
import json
import sys
import time

sys.path.insert(0, "/lot1/lib")
from bson import Decimal128, ObjectId  # noqa: E402

from catalogue_config import extraire_marque  # noqa: E402
from outils import connecter, decimales, json_compact, maintenant, plan, tableau, texte, titre  # noqa: E402
from pipelines import NB_AVIS_EMBARQUES, avis_embarque, reconcilier_produit, resume_avis  # noqa: E402

db = connecter()
SOURCE = "/lot1/data/raw/pc-part-dataset/json"
PRODUIT = "MB-000131"   # MSI PRO B760-P WIFI DDR4
AVIS_DEMO = ObjectId("dddddddddddddddddddd0001")
PROMO, CIBLE, MARQUE, ARRET = "MON-000351", "CPU-000248", "teamgroup", "CPU-000060"


def resume(p):
    r = p["rating"]
    return (f"note {texte(r['average'])} | {r['count']} avis | somme {r['sum']} | "
            f"répartition {json_compact(r['distribution'])}")


def ms_depuis(debut):
    return int((time.time() - debut) * 1000)


def ligne_source(sku):
    """La ligne du jeu public dont la fiche est issue : elle fait foi pour la remise en état."""
    origine = db.products.find_one({"_id": sku}, {"source": 1})["source"]
    with open(f"{SOURCE}/{origine['file']}", encoding="utf-8") as f:
        return json.load(f)[origine["line"] - 1]


def renommer_produit(nom):
    """Renomme CIBLE, puis chaque fiche qui le recopie parmi ses compatibles (Extended Reference)."""
    db.products.update_one({"_id": CIBLE}, {"$set": {"name": nom, "updatedAt": maintenant()}})
    return db.products.update_many(
        {"compatibleProducts._id": CIBLE},
        {"$set": {"compatibleProducts.$[c].name": nom, "updatedAt": maintenant()}},
        array_filters=[{"c._id": CIBLE}],
    ).modified_count


def changer_marque(nom):
    db.brands.update_one({"_id": MARQUE}, {"$set": {"name": nom}})
    return db.products.update_many({"brand.id": MARQUE}, {"$set": {"brand.name": nom, "updatedAt": maintenant()}})


def annuler():
    """Remet dans leur état initial les données que ce script modifie. Renvoie le nombre de corrections."""
    corrections = 0
    if db.reviews.delete_one({"_id": AVIS_DEMO}).deleted_count:      # E1, E2 : avis de démonstration
        reconcilier_produit(db, PRODUIT)
        corrections += 1
    corrections += db.products.update_one({"_id": PROMO, "price.regular": {"$exists": True}}, [   # E3
        {"$set": {"price.amount": "$price.regular", "updatedAt": "$$NOW"}},
        {"$unset": "price.regular"},
    ]).modified_count
    nom = ligne_source(CIBLE)["name"]                                 # E4 : nom du jeu source
    if db.products.find_one({"_id": CIBLE})["name"] != nom:
        renommer_produit(nom)
        corrections += 1
    exemple = db.products.find_one({"brand.id": MARQUE}, {"_id": 1})  # E5 : marque déduite du nom source
    marque = extraire_marque(ligne_source(exemple["_id"])["name"])
    if db.brands.find_one({"_id": MARQUE})["name"] != marque:
        changer_marque(marque)
        corrections += 1
    if db.products.find_one({"_id": ARRET})["status"] != "active":    # E6 : remise en vente au prix source
        prix = Decimal128(decimales(ligne_source(ARRET)["price"], 2))
        db.products.update_one({"_id": ARRET}, {"$set": {"status": "active",
                                                         "price": {"amount": prix, "currency": "USD"},
                                                         "updatedAt": maintenant()}})
        corrections += 1
    return corrections


corrections = annuler()
if "--annuler" in sys.argv:
    print(f"   Écritures de 02-ecritures.py retirées : données dans leur état initial ({corrections} correction(s)).")
    sys.exit(0)
print(f"   Point de départ : état initial ({corrections} correction(s) d'une exécution précédente).")
print("   Les écritures ci-dessous restent dans la base.")

# -----------------------------------------------------------------------------------------
titre("E1 - Dépôt d'un avis : écriture dans `reviews` uniquement (statut pending)",
      "Tant qu'il n'est pas modéré, l'avis n'apparaît pas sur la fiche : la fiche n'est pas touchée.")
avant = db.products.find_one({"_id": PRODUIT}, {"rating": 1})
db.reviews.insert_one({
    "_id": AVIS_DEMO,
    "productId": PRODUIT,
    "customer": {"id": "CUST-000001", "displayName": "Julien B."},
    "rating": 5,
    "title": "Avis de démonstration",
    "body": "Carte mère stable, BIOS clair, Wi-Fi intégré pratique. Montage sans difficulté.",
    "status": "pending",
    "verifiedPurchase": True,
    "helpfulVotes": 0,
    "synthetic": True,
    "createdAt": maintenant(),
})
print(f"   Avis inséré (pending). Fiche {PRODUIT} inchangée : {resume(avant)}")

# -----------------------------------------------------------------------------------------
titre("E2 - Modération : l'avis est approuvé, la fiche est mise à jour en UNE écriture atomique",
      "Mise à jour par pipeline : compteurs (Computed) + avis en tête de liste, coupée à 5 (Subset).")
db.reviews.update_one({"_id": AVIS_DEMO, "status": "pending"}, {"$set": {"status": "approved"}})
avis = db.reviews.find_one({"_id": AVIS_DEMO})
db.products.update_one({"_id": PRODUIT}, [
    {
        "$set": {
            "rating.count": {"$add": ["$rating.count", 1]},
            "rating.sum": {"$add": ["$rating.sum", avis["rating"]]},
            "rating.average": {"$round": [{"$divide": [{"$add": ["$rating.sum", avis["rating"]]},
                                                       {"$add": ["$rating.count", 1]}]}, 2]},
            f"rating.distribution.{avis['rating']}": {"$add": [f"$rating.distribution.{avis['rating']}", 1]},
            # $literal : le texte de l'avis ne doit jamais être interprété comme une expression
            "recentReviews": {"$slice": [{"$concatArrays": [[{"$literal": avis_embarque(avis)}], "$recentReviews"]},
                                         NB_AVIS_EMBARQUES]},
            "updatedAt": "$$NOW",
        },
    },
])
apres = db.products.find_one({"_id": PRODUIT}, {"rating": 1, "recentReviews": 1})
print(f"   Avant : {resume(avant)}")
print(f"   Après : {resume(apres)}")
print(f"   Avis embarqués : {len(apres['recentReviews'])}, le premier est \"{apres['recentReviews'][0]['title']}\"")
print("   Pas de transaction multi-documents : `reviews` est la source de vérité et le résumé est")
print("   recalculable (E7). En cas d'échec entre les deux écritures, la réconciliation corrige.")

# -----------------------------------------------------------------------------------------
titre("E3 - Changement de prix : promotion -15 %",
      "Le prix n'est recopié dans AUCUN autre document : un changement de prix = 1 seule écriture.")
r1 = db.products.update_one({"_id": PROMO, "status": "active"}, [
    {"$set": {"price.regular": {"$ifNull": ["$price.regular", "$price.amount"]}}},  # prix barré
    {"$set": {"price.amount": {"$round": [{"$multiply": ["$price.regular", Decimal128("0.85")]}, 2]},
              "updatedAt": "$$NOW"}},
])
p = db.products.find_one({"_id": PROMO}, {"name": 1, "price": 1})
print(f"   {p['name']} : {p['price']['amount']} {p['price']['currency']} au lieu de {p['price']['regular']}"
      f" ({r1.modified_count} document modifié)")
print("   Fin de promotion (non appliquée ici) : price.amount reprend price.regular, puis $unset de price.regular.")

# -----------------------------------------------------------------------------------------
titre("E4 - Coût de l'Extended Reference : renommer un produit recopié dans d'autres fiches",
      "Les fiches qui listent ce produit parmi leurs compatibles sont retrouvées par ix_compatibles_inverse.")
nom_initial = db.products.find_one({"_id": CIBLE})["name"]
n = renommer_produit(nom_initial + " (boîte)")
print(f"   \"{nom_initial}\" renommé \"{nom_initial} (boîte)\" : 1 fiche + {n} fiches qui le recopient mises à jour")
plan(db.products.find({"compatibleProducts._id": CIBLE}).explain())

# -----------------------------------------------------------------------------------------
titre("E5 - Renommage d'une marque : écriture en masse, mais opération très rare",
      "Compromis assumé : on duplique le nom de la marque (lu à chaque affichage) au prix de ce update_many.")
nom_marque = db.brands.find_one({"_id": MARQUE})["name"]
debut_marque = time.time()
res = changer_marque("TeamGroup")
print(f"   \"{nom_marque}\" -> \"TeamGroup\" : {res.modified_count} fiches modifiées en {ms_depuis(debut_marque)} ms")

# -----------------------------------------------------------------------------------------
titre("E6 - Fin de commercialisation : le produit sort des listings, sa fiche reste en ligne",
      "Les index de listing étant partiels (status: active), il en sort sans autre traitement.")
filtre_am5 = {"category.id": "processeurs", "status": "active", "specs": {"$elemMatch": {"k": "socket", "v": "AM5"}}}
nb_avant = db.products.count_documents(filtre_am5)
db.products.update_one({"_id": ARRET}, {"$set": {"status": "discontinued", "price": None, "updatedAt": maintenant()}})
nb_apres = db.products.count_documents(filtre_am5)
print(f"   Processeurs AM5 en vente : {nb_avant} -> {nb_apres} ; fiche toujours accessible : "
      f"{db.products.count_documents({'_id': ARRET})}")

# -----------------------------------------------------------------------------------------
titre("E7 - Job de réconciliation : recalcul complet des résumés d'avis depuis `reviews`",
      "Idempotent ; vérifie que la mise à jour incrémentale (E2) donne le même résultat.")


def normaliser_resume(r):
    return json_compact({"count": r["count"], "sum": r["sum"], "average": r["average"],
                         "distribution": r["distribution"]})


incremental = normaliser_resume(db.products.find_one({"_id": PRODUIT}, {"rating": 1})["rating"])
debut_job = time.time()
list(db.reviews.aggregate(resume_avis(), allowDiskUse=True))
recalcule = normaliser_resume(db.products.find_one({"_id": PRODUIT}, {"rating": 1})["rating"])
print(f"   Recalcul de {db.products.count_documents({'rating.count': {'$gt': 0}})} fiches en {ms_depuis(debut_job)} ms")
print(f"   {PRODUIT} incrémental : {incremental}")
print(f"   {PRODUIT} recalculé   : {recalcule}")
print(f"   Identiques : {texte(incremental == recalcule)}")

# -----------------------------------------------------------------------------------------
fiche = db.products.find_one({"_id": PRODUIT}, {"rating": 1})["rating"]
titre("À vérifier dans Compass (mongodb://localhost:27018, base ldlc_catalogue)",
      "Ces écritures restent dans la base. Pour les retirer : ... 02-ecritures.py --annuler")
tableau([
    {"collection": "reviews", "filtre": '{ _id: ObjectId("dddddddddddddddddddd0001") }',
     "ce que l'on voit": "l'avis de démonstration, approuvé"},
    {"collection": "products", "filtre": '{ _id: "MB-000131" }',
     "ce que l'on voit": f"note {texte(fiche['average'])} sur {fiche['count']} avis, l'avis en tête de recentReviews"},
    {"collection": "products", "filtre": '{ _id: "MON-000351" }',
     "ce que l'on voit": f"price.amount {p['price']['amount']}, prix barré price.regular {p['price']['regular']}"},
    {"collection": "products", "filtre": '{ "compatibleProducts._id": "CPU-000248" }',
     "ce que l'on voit": f"{n} fiches : le compatible s'appelle « … (boîte) »"},
    {"collection": "products", "filtre": '{ "brand.id": "teamgroup" }',
     "ce que l'on voit": f"{res.modified_count} fiches : brand.name vaut TeamGroup"},
    {"collection": "products", "filtre": '{ _id: "CPU-000060" }',
     "ce que l'on voit": "status discontinued, price null"},
], ["collection", "filtre", "ce que l'on voit"])
