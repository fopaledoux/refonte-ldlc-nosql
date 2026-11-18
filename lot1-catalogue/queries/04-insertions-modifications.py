"""
Lot 1 — Requêtes types : INSERTIONS et MODIFICATIONS, opération par opération

Insertions (I1 à I5) : insert_one, insert_many ordonné ou non, upsert, doublons refusés,
remplacement idempotent d'un import.
Modifications (U1 à U8) : $set, $inc, opérateurs de tableau ($, $push, $pull, $addToSet),
modification en masse par pipeline, mise à jour conditionnelle (concurrence),
remplacement complet, anonymisation RGPD dans les copies embarquées.

Lancement : docker compose run --rm mongo-init python /lot1/queries/04-insertions-modifications.py [--annuler]
  sans option : les insertions et modifications RESTENT dans la base (à vérifier dans
                Compass : les filtres sont affichés à la fin). Chaque exécution repart de
                l'état initial, donc le résultat est le même à chaque fois ;
  --annuler   : retire tout ce que ce script a créé ou modifié.
Les produits créés utilisent une plage de SKU réservée (900001 et suivants).
"""
import sys
import time

sys.path.insert(0, "/lot1/lib")
from bson import Decimal128, ObjectId  # noqa: E402
from pymongo import ReturnDocument  # noqa: E402
from pymongo.errors import BulkWriteError, DuplicateKeyError  # noqa: E402

from catalogue_config import slugify  # noqa: E402
from outils import connecter, maintenant, plan, tableau, texte, titre  # noqa: E402
from pipelines import nom_affiche, reconcilier_produit  # noqa: E402

db = connecter()
NOUVEAU, IMPORT = "GPU-900001", "GPU-900002"
KITS = ["RAM-900001", "RAM-900002", "RAM-900003"]
MARQUE_DEMO = "demo-marque"
AVIS_VOTE = ObjectId("dddddddddddddddddddd0004")
ANONYME = "Client anonymisé"
FILTRE_PROMO = {"brand.id": "corsair", "category.id": "memoire-ram", "status": "active",
                "specs": {"$elemMatch": {"k": "ddr_generation", "v": "DDR4"}}}


def annuler():
    """Retire ce que ce script crée ou modifie. Renvoie le nombre de corrections."""
    corrections = 0
    if db.products.delete_one({"_id": NOUVEAU}).deleted_count:                       # I1 et ses compteurs
        db.categories.update_one({"_id": "cartes-graphiques"}, {"$inc": {"productCount": -1}})
        db.brands.update_one({"_id": "asus"}, {"$inc": {"productCount": -1, "activeProductCount": -1}})
        corrections += 1
    corrections += db.products.delete_many({"_id": {"$in": KITS + [IMPORT]}}).deleted_count  # I2, I5
    corrections += db.brands.delete_one({"_id": MARQUE_DEMO}).deleted_count             # I3
    corrections += db.reviews.delete_one({"_id": AVIS_VOTE}).deleted_count              # U2
    corrections += db.products.update_many({**FILTRE_PROMO, "price.regular": {"$exists": True}}, [   # U5
        {"$set": {"price.amount": "$price.regular", "updatedAt": "$$NOW"}},
        {"$unset": "price.regular"},
    ]).modified_count
    for client in db.reviews.distinct("customer.id", {"customer.displayName": ANONYME}):  # U8
        nom = nom_affiche(int(client.split("-")[1]))
        ids = [a["_id"] for a in db.reviews.find({"customer.id": client}, {"_id": 1})]
        db.reviews.update_many({"customer.id": client}, {"$set": {"customer.displayName": nom}})
        db.products.update_many({"recentReviews.reviewId": {"$in": ids}},
                                {"$set": {"recentReviews.$[a].customer": nom}},
                                array_filters=[{"a.reviewId": {"$in": ids}}])
        corrections += 1
    return corrections


def fiche_gpu(sku, nom):
    """Une fiche complète, construite comme à l'import : marque et catégorie recopiées."""
    categorie = db.categories.find_one({"_id": "cartes-graphiques"}, {"name": 1, "path": 1})
    marque = db.brands.find_one({"_id": "asus"}, {"name": 1})
    instant = maintenant()
    return {
        "_id": sku, "schemaVersion": 1, "type": "physical", "name": nom,
        "slug": f"{slugify(nom)}-{sku.lower()}",
        "brand": {"id": marque["_id"], "name": marque["name"]},                       # Extended Reference
        "category": {"id": categorie["_id"], "name": categorie["name"], "path": categorie["path"]},  # Tree
        "status": "active",
        "price": {"amount": Decimal128("899.99"), "currency": "USD"},
        "specs": [
            {"k": "chipset", "v": "GeForce RTX 5070 Ti"},
            {"k": "memory_gb", "v": 16, "u": "Go"},
            {"k": "core_clock", "v": 2295, "u": "MHz"},
            {"k": "boost_clock", "v": 2588, "u": "MHz"},
            {"k": "length_mm", "v": 305, "u": "mm"},
            {"k": "color", "v": "Black"},
        ],
        "rating": {"average": None, "count": 0, "sum": 0, "distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}},
        "recentReviews": [], "compatibleProducts": [],
        "source": {"dataset": "demo", "note": "fiche créée par 04-insertions-modifications.py"},
        "createdAt": instant, "updatedAt": instant,
    }


def kit(sku, nom, prix):
    instant = maintenant()
    return {
        "_id": sku, "schemaVersion": 1, "type": "physical", "name": nom, "slug": f"{slugify(nom)}-{sku.lower()}",
        "brand": {"id": "corsair", "name": "Corsair"},
        "category": {"id": "memoire-ram", "name": "Mémoire RAM", "path": ["composants", "memoire-ram"]},
        "status": "active", "price": {"amount": prix, "currency": "USD"},
        "specs": [{"k": "ddr_generation", "v": "DDR5"}, {"k": "capacity_gb", "v": 32, "u": "Go"}],
        "rating": {"average": None, "count": 0, "sum": 0, "distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}},
        "recentReviews": [], "compatibleProducts": [], "createdAt": instant, "updatedAt": instant,
    }


def specs(sku):
    return ", ".join(f"{s['k']}={texte(s['v'])}{s.get('u', '')}" for s in db.products.find_one({"_id": sku})["specs"])


def erreur(e):
    """Première ligne du message d'erreur renvoyé par le serveur."""
    details = getattr(e, "details", None) or {}
    return (details.get("errmsg") or str(e)).splitlines()[0][:150]


corrections = annuler()
if "--annuler" in sys.argv:
    print(f"   Insertions et modifications de 04 retirées : données dans leur état initial ({corrections} correction(s)).")
    sys.exit(0)
print(f"   Point de départ : état initial ({corrections} correction(s) d'une exécution précédente).")
print("   Les insertions et modifications ci-dessous restent dans la base.")

# =========================================================================================
#  INSERTIONS
# =========================================================================================
titre("I1 - Créer une fiche produit (insert_one), et mettre à jour les compteurs qui en dépendent",
      "La marque et la catégorie sont lues puis recopiées (Extended Reference) ; le validateur contrôle la fiche.")
compte_avant = db.categories.find_one({"_id": "cartes-graphiques"})["productCount"]
resultat = db.products.insert_one(fiche_gpu(NOUVEAU, "Asus TUF Gaming RTX 5070 Ti OC"))
# Computed pattern : les compteurs de la catégorie et de la marque suivent l'insertion
db.categories.update_one({"_id": "cartes-graphiques"}, {"$inc": {"productCount": 1}})
db.brands.update_one({"_id": "asus"}, {"$inc": {"productCount": 1, "activeProductCount": 1}})
print(f"   Inséré : {resultat.inserted_id} ; cartes graphiques au catalogue : {compte_avant} -> "
      f"{db.categories.find_one({'_id': 'cartes-graphiques'})['productCount']}")
filtre = {"category.id": "cartes-graphiques", "status": "active",
          "specs": {"$elemMatch": {"k": "chipset", "v": "GeForce RTX 5070 Ti"}}, "price.amount": {"$lte": Decimal128("900")}}
print(f"   Visible tout de suite dans les facettes (RTX 5070 Ti à 900 USD au plus) : "
      f"{db.products.count_documents({**filtre, '_id': NOUVEAU})} résultat")
plan(db.products.find(filtre).explain())

# -----------------------------------------------------------------------------------------
titre("I2 - Insérer un lot de fiches (insert_many) : ordonné ou non ?",
      "Le 2e kit a un prix en texte au lieu d'un Decimal128 : le validateur le refuse.")
lot = [kit(KITS[0], "Corsair Vengeance DDR5 32 Go 6000 MHz", Decimal128("109.99")),
       kit(KITS[1], "Corsair Vengeance DDR5 32 Go 6400 MHz", "119.99"),          # invalide
       kit(KITS[2], "Corsair Dominator DDR5 32 Go 6600 MHz", Decimal128("149.99"))]
for ordonne in (True, False):
    if ordonne is False:
        db.products.delete_many({"_id": {"$in": KITS}})       # on rejoue le même lot depuis zéro
    try:
        db.products.insert_many(lot, ordered=ordonne)
    except BulkWriteError as e:
        d = e.details
        n = d["nInserted"]
        print(f"   ordered={texte(ordonne):<5} : {n} inséré{'s' if n > 1 else ''}, refusé à l'index "
              f"{d['writeErrors'][0]['index']} ({d['writeErrors'][0]['errmsg']})")
    print(f"                   kits présents ensuite : {db.products.count_documents({'_id': {'$in': KITS}})}")
print("   -> ordered=True s'arrête à la première erreur ; ordered=False insère tout ce qui est valide.")
print(f"   Restent dans la base : {', '.join(k['_id'] for k in db.products.find({'_id': {'$in': KITS}}, {'_id': 1}))}")

# -----------------------------------------------------------------------------------------
titre("I3 - Créer une marque si elle n'existe pas (upsert + $setOnInsert)",
      "Rejouer la même écriture ne crée pas de doublon et ne modifie rien : l'opération est idempotente.")
for passage in (1, 2):
    r = db.brands.update_one(
        {"_id": MARQUE_DEMO},
        {"$setOnInsert": {"name": "Marque Démo", "productCount": 0, "activeProductCount": 0, "categories": []}},
        upsert=True)
    print(f"   Passage {passage} : créée={texte(r.upserted_id is not None)}, trouvée={r.matched_count}, "
          f"modifiée={r.modified_count}")

# -----------------------------------------------------------------------------------------
titre("I4 - Doublons refusés : même SKU, puis même URL",
      "Le _id est unique par nature ; l'URL (slug) l'est grâce à l'index unique ux_slug.")
try:
    db.products.insert_one(fiche_gpu(NOUVEAU, "Asus TUF Gaming RTX 5070 Ti OC"))
except DuplicateKeyError as e:
    print(f"   Même SKU  : REFUSÉ  -> {erreur(e)}")
doublon = fiche_gpu(IMPORT, "Asus TUF Gaming RTX 5070 Ti OC")
doublon["slug"] = db.products.find_one({"_id": NOUVEAU})["slug"]
try:
    db.products.insert_one(doublon)
except DuplicateKeyError as e:
    print(f"   Même URL  : REFUSÉ  -> {erreur(e)}")

# -----------------------------------------------------------------------------------------
titre("I5 - Import d'un flux fournisseur rejouable (replace_one + upsert)",
      "Une fiche absente est créée, une fiche présente est remplacée : rejouer l'import donne le même état.")
fiche_flux = fiche_gpu(IMPORT, "Asus Prime RTX 5070 Ti OC")
for libelle, prix in (("1er import, fiche absente     ", "899.99"), ("même flux rejoué              ", "899.99"),
                      ("flux du lendemain (prix baissé)", "849.99")):
    fiche_flux["price"]["amount"] = Decimal128(prix)
    r = db.products.replace_one({"_id": IMPORT}, fiche_flux, upsert=True)
    print(f"   {libelle} : créée={texte(r.upserted_id is not None)}, remplacée={r.modified_count}, "
          f"fiches GPU-9000xx : {db.products.count_documents({'_id': {'$regex': '^GPU-9000'}})}")

# =========================================================================================
#  MODIFICATIONS
# =========================================================================================
titre("U1 - Modifier des champs ($set) et horodater côté serveur ($currentDate)",
      "Une modification qui ne change rien est reconnue : la fiche est trouvée, mais pas réécrite.")
nouveautes = {"price.amount": Decimal128("879.99"), "name": "Asus TUF Gaming RTX 5070 Ti OC 16 Go"}
r = db.products.update_one({"_id": NOUVEAU}, {"$set": nouveautes, "$currentDate": {"updatedAt": True}})
print(f"   $set + $currentDate      : trouvée={r.matched_count}, modifiée={r.modified_count}")
r = db.products.update_one({"_id": NOUVEAU}, {"$set": nouveautes})
print(f"   Même $set, une 2e fois   : trouvée={r.matched_count}, modifiée={r.modified_count}")
p = db.products.find_one({"_id": NOUVEAU}, {"name": 1, "price": 1})
print(f"   Résultat : {p['name']} à {p['price']['amount']} {p['price']['currency']}")
print("   -> une valeur déjà en place n'est pas réécrite (modifiée=0) ; $currentDate, lui, change toujours la date")

# -----------------------------------------------------------------------------------------
titre("U2 - Incrémenter un compteur sans le lire ($inc), et récupérer la nouvelle valeur",
      "Un vote « avis utile » : atomique, donc juste même si mille clients votent en même temps.")
db.reviews.insert_one({
    "_id": AVIS_VOTE, "productId": NOUVEAU, "customer": {"id": "CUST-000001", "displayName": nom_affiche(1)},
    "rating": 5, "title": "Silencieuse et très rapide", "body": "Aucun bruit en charge, gros gain en 1440p.",
    "status": "approved", "verifiedPurchase": True, "helpfulVotes": 0, "synthetic": True, "createdAt": maintenant(),
})
reconcilier_produit(db, NOUVEAU)                      # la fiche affiche son premier avis (Computed, Subset)
apres = db.reviews.find_one_and_update({"_id": AVIS_VOTE}, {"$inc": {"helpfulVotes": 1}},
                                       projection={"helpfulVotes": 1, "title": 1},
                                       return_document=ReturnDocument.AFTER)
print(f"   Avis « {apres['title']} » sur {NOUVEAU} : 0 -> {apres['helpfulVotes']} vote utile "
      "(find_one_and_update renvoie l'avis après modification, en un aller-retour)")

# -----------------------------------------------------------------------------------------
titre("U3 - Modifier, ajouter et retirer un attribut dans le tableau specs",
      "Attribute pattern : opérateur positionnel $, $push conditionnel, $pull.")
print(f"   Avant   : {specs(NOUVEAU)}")
db.products.update_one({"_id": NOUVEAU, "specs.k": "boost_clock"}, {"$set": {"specs.$.v": 2610}})
db.products.update_one({"_id": NOUVEAU, "specs.k": {"$ne": "pcie_version"}},
                       {"$push": {"specs": {"k": "pcie_version", "v": "5.0"}}})       # idempotent
db.products.update_one({"_id": NOUVEAU}, {"$pull": {"specs": {"k": "color"}}})
print(f"   Après   : {specs(NOUVEAU)}")
print("   -> boost_clock modifié sur place, pcie_version ajouté (une seule fois), color retiré")

# -----------------------------------------------------------------------------------------
titre("U4 - Ajouter des valeurs à une liste sans doublon ($addToSet), en retirer une ($pull)")
for passage in (1, 2):
    r = db.brands.update_one({"_id": MARQUE_DEMO},
                             {"$addToSet": {"categories": {"$each": ["cartes-graphiques", "memoire-ram"]}}})
    print(f"   $addToSet, passage {passage} : modifiée={r.modified_count}, catégories "
          f"{db.brands.find_one({'_id': MARQUE_DEMO})['categories']}")
db.brands.update_one({"_id": MARQUE_DEMO}, {"$pull": {"categories": "memoire-ram"}})
print(f"   $pull memoire-ram : catégories {db.brands.find_one({'_id': MARQUE_DEMO})['categories']}")

# -----------------------------------------------------------------------------------------
titre("U5 - Modification en masse par critères : -10 % sur les kits Corsair DDR4",
      "update_many + pipeline : le prix barré est gardé dans price.regular.")
exemple = db.products.find_one(FILTRE_PROMO, {"name": 1, "price": 1}, sort=[("_id", 1)])
debut = time.time()
r = db.products.update_many(FILTRE_PROMO, [
    {"$set": {"price.regular": {"$ifNull": ["$price.regular", "$price.amount"]}}},
    {"$set": {"price.amount": {"$round": [{"$multiply": ["$price.regular", Decimal128("0.90")]}, 2]},
              "updatedAt": "$$NOW"}},
])
duree = int((time.time() - debut) * 1000)
promo = r.modified_count
apres = db.products.find_one({"_id": exemple["_id"]}, {"price": 1})
print(f"   {r.matched_count} fiches trouvées, {promo} modifiées en {duree} ms")
print(f"   Exemple {exemple['_id']} ({exemple['name']}) : {exemple['price']['amount']} -> "
      f"{apres['price']['amount']} USD (prix barré {apres['price']['regular']})")
plan(db.products.find(FILTRE_PROMO).explain())
print("   Fin de promotion (non appliquée ici) : price.amount reprend price.regular, puis $unset de price.regular.")

# -----------------------------------------------------------------------------------------
titre("U6 - Mise à jour conditionnelle : deux écrans du back-office modifient le même prix",
      "Le filtre contient la valeur lue : si quelqu'un l'a changée entre-temps, rien n'est écrasé.")
lu = db.products.find_one({"_id": NOUVEAU}, {"price": 1})["price"]["amount"]
db.products.update_one({"_id": NOUVEAU}, {"$set": {"price.amount": Decimal128("869.99")}})     # l'autre écran
r = db.products.update_one({"_id": NOUVEAU, "price.amount": lu}, {"$set": {"price.amount": Decimal128("859.99")}})
print(f"   Prix lu : {lu} ; un autre écran le passe à 869.99 ; notre écriture « si le prix vaut encore {lu} » : "
      f"trouvée={r.matched_count}")
print(f"   -> conflit détecté, le prix reste {db.products.find_one({'_id': NOUVEAU})['price']['amount']} : "
      "l'écran relit la fiche et propose de recommencer")

# -----------------------------------------------------------------------------------------
titre("U7 - Remplacer toute la fiche (replace_one) ou modifier des champs ($set) ?",
      f"replace_one écrit le document fourni tel quel : les champs oubliés disparaissent (fiche {IMPORT}).")
champs_avant = sorted(db.products.find_one({"_id": IMPORT}).keys())
incomplete = {k: v for k, v in fiche_flux.items() if k not in ("rating", "recentReviews", "compatibleProducts")}
db.products.replace_one({"_id": IMPORT}, incomplete)
champs_apres = sorted(db.products.find_one({"_id": IMPORT}).keys())
print(f"   Champs perdus : {sorted(set(champs_avant) - set(champs_apres))}")
print("   -> replace_one est réservé aux imports complets (I5) ; une modification passe par $set")

# -----------------------------------------------------------------------------------------
titre("U8 - Anonymiser un client (RGPD), y compris dans les copies embarquées",
      "Le nom est recopié dans chaque fiche qui affiche son avis (Subset) : update_many avec arrayFilters.")
fiche = db.products.find_one({"recentReviews.0": {"$exists": True}, "category.id": "cartes-meres",
                              "rating.count": {"$gte": 3}}, {"recentReviews": 1}, sort=[("_id", 1)])
client = db.reviews.find_one({"_id": fiche["recentReviews"][0]["reviewId"]})["customer"]
ids = [a["_id"] for a in db.reviews.find({"customer.id": client["id"]}, {"_id": 1})]
r1 = db.reviews.update_many({"customer.id": client["id"]}, {"$set": {"customer.displayName": ANONYME}})
r2 = db.products.update_many({"recentReviews.reviewId": {"$in": ids}},
                             {"$set": {"recentReviews.$[a].customer": ANONYME}},
                             array_filters=[{"a.reviewId": {"$in": ids}}])
print(f"   {client['id']} ({client['displayName']}) : {r1.modified_count} avis dans reviews, "
      f"{r2.modified_count} fiche(s) qui recopient un de ses avis")
tableau([{"fiche": p["_id"], "auteur affiché": next(a["customer"] for a in p["recentReviews"] if a["reviewId"] in ids)}
         for p in db.products.find({"recentReviews.reviewId": {"$in": ids}}, {"recentReviews": 1})],
        ["fiche", "auteur affiché"])
print("   -> c'est le coût des copies : chaque donnée recopiée doit être retrouvable (ici par reviewId)")

# -----------------------------------------------------------------------------------------
titre("À vérifier dans Compass (mongodb://localhost:27018, base ldlc_catalogue)",
      "Ces données restent dans la base. Pour les retirer : ... 04-insertions-modifications.py --annuler")
tableau([
    {"collection": "products", "filtre": '{ _id: { $regex: "^(GPU|RAM)-9" } }',
     "ce que l'on voit": f"4 fiches créées : {NOUVEAU} (U1, U2, U3, U6), {IMPORT} (U7), RAM-900001 et RAM-900003"},
    {"collection": "categories", "filtre": '{ _id: "cartes-graphiques" }',
     "ce que l'on voit": f"productCount {db.categories.find_one({'_id': 'cartes-graphiques'})['productCount']}"},
    {"collection": "brands", "filtre": f'{{ _id: "{MARQUE_DEMO}" }}',
     "ce que l'on voit": "la marque créée (I3), catégories [cartes-graphiques] (U4)"},
    {"collection": "reviews", "filtre": '{ _id: ObjectId("dddddddddddddddddddd0004") }',
     "ce que l'on voit": "l'avis de la fiche créée, helpfulVotes 1 (U2)"},
    {"collection": "products", "filtre": '{ "brand.id": "corsair", "price.regular": { $exists: true } }',
     "ce que l'on voit": f"{promo} kits DDR4 à -10 %, prix barré dans price.regular (U5)"},
    {"collection": "reviews", "filtre": f'{{ "customer.id": "{client["id"]}" }}',
     "ce que l'on voit": f"{r1.modified_count} avis signés « {ANONYME} » (U8)"},
    {"collection": "products", "filtre": f'{{ "recentReviews.customer": "{ANONYME}" }}',
     "ce que l'on voit": f"{r2.modified_count} fiches dont un avis embarqué est anonymisé (U8)"},
], ["collection", "filtre", "ce que l'on voit"])
