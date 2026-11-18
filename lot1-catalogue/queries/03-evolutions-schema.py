"""
Lot 1 — Requêtes types : ÉVOLUTIONS DU SCHÉMA SANS MIGRATION

Contrainte du sujet : « les fiches doivent évoluer sans migration (ajout d'attributs,
nouvelles catégories) ».
Lancement : docker compose run --rm mongo-init python /lot1/queries/03-evolutions-schema.py
Les évolutions sont idempotentes (relançables) et restent en place.
"""
import sys

sys.path.insert(0, "/lot1/lib")
from bson import Decimal128  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from outils import connecter, maintenant, plan, tableau, texte, titre  # noqa: E402

db = connecter()


def essayer(libelle, action):
    try:
        action()
        print(f"   ACCEPTÉ  {libelle}")
    except PyMongoError as e:
        details = getattr(e, "details", None) or {}
        message = details.get("errmsg") or str(e)
        print(f"   REFUSÉ   {libelle}\n            -> {details.get('codeName') or type(e).__name__} : "
              f"{message.splitlines()[0]}")


# -----------------------------------------------------------------------------------------
titre("V1 - Nouvelle catégorie avec des attributs inédits : aucune commande de structure",
      "Pas d'ALTER TABLE, pas de nouvel index : une ligne dans `categories`, puis des produits.")
r_categorie = db.categories.replace_one({"_id": "casques-vr"}, {
    "_id": "casques-vr", "name": "Casques de réalité virtuelle", "parent": "peripheriques",
    "path": ["peripheriques", "casques-vr"], "level": 2, "isLeaf": True, "productCount": 1,
    "specTemplate": [
        {"k": "resolution_per_eye", "label": "Résolution par œil", "unit": None, "filterable": True, "derived": False},
        {"k": "refresh_rate", "label": "Fréquence de rafraîchissement", "unit": "Hz", "filterable": True, "derived": False},
        {"k": "standalone", "label": "Autonome (sans PC)", "unit": None, "filterable": True, "derived": False},
        {"k": "tracking", "label": "Suivi des mouvements", "unit": None, "filterable": True, "derived": False},
        {"k": "storage_gb", "label": "Stockage", "unit": "Go", "filterable": True, "derived": False},
    ],
}, upsert=True)
instant = maintenant()
db.products.replace_one({"_id": "VR-000001"}, {
    "_id": "VR-000001", "schemaVersion": 1, "type": "physical",
    "name": "Meta Quest 3 512 Go", "slug": "meta-quest-3-512-go-vr-000001",
    "brand": {"id": "meta", "name": "Meta"},
    "category": {"id": "casques-vr", "name": "Casques de réalité virtuelle", "path": ["peripheriques", "casques-vr"]},
    "status": "active",
    "price": {"amount": Decimal128("549.99"), "currency": "USD"},
    "specs": [
        {"k": "resolution_per_eye", "v": "2064x2208"},
        {"k": "refresh_rate", "v": 120, "u": "Hz"},
        {"k": "standalone", "v": True},
        {"k": "tracking", "v": "Inside-out"},
        {"k": "storage_gb", "v": 512, "u": "Go"},
    ],
    "rating": {"average": None, "count": 0, "sum": 0, "distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}},
    "recentReviews": [], "compatibleProducts": [],
    "source": {"dataset": "demo", "note": "produit ajouté pour illustrer l'évolution du catalogue"},
    "createdAt": instant, "updatedAt": instant,
}, upsert=True)
db.brands.update_one({"_id": "meta"}, {"$setOnInsert": {"name": "Meta", "productCount": 1, "activeProductCount": 1,
                                                        "categories": ["casques-vr"]}}, upsert=True)

# La nouvelle catégorie, relue dans la base
categorie = db.categories.find_one({"_id": "casques-vr"})
etat = "créée" if r_categorie.upserted_id else "déjà présente (exécution précédente), réécrite à l'identique"
print(f"   Catégorie {etat} : {categorie['_id']} « {categorie['name']} »")
print(f"     parent {categorie['parent']} · chemin {' > '.join(categorie['path'])} · niveau {categorie['level']}"
      f" · feuille {texte(categorie['isLeaf'])} · {categorie['productCount']} produit")
print("     Gabarit d'attributs (specTemplate), stocké une seule fois dans la catégorie :")
tableau([{"clé": s["k"], "libellé": s["label"], "unité": s["unit"], "filtrable": s["filterable"]}
         for s in categorie["specTemplate"]], ["clé", "libellé", "unité", "filtrable"])
soeurs = [c["_id"] for c in db.categories.find({"parent": categorie["parent"]}, {"_id": 1}).sort("_id", 1)]
print(f"     Sous-catégories de {categorie['parent']} : "
      + ", ".join(f"[{c}]" if c == categorie["_id"] else c for c in soeurs))
vr = db.products.find_one({"_id": "VR-000001"}, {"name": 1, "price": 1, "brand": 1})
print(f"   Premier produit : {vr['_id']} « {vr['name']} », {vr['price']['amount']} {vr['price']['currency']},"
      f" marque {vr['brand']['name']} (créée si absente, par upsert)\n")

filtre_vr = {"category.id": "casques-vr", "status": "active", "specs": {"$all": [
    {"$elemMatch": {"k": "standalone", "v": True}}, {"$elemMatch": {"k": "refresh_rate", "v": {"$gte": 90}}},
]}}
print("   Filtre immédiat sur les nouveaux attributs (autonome, >= 90 Hz) :")
for x in db.products.find(filtre_vr, {"name": 1}):
    print(f"     {x['_id']}  {x['name']}")
plan(db.products.find(filtre_vr).explain())
print("   Le listing \"Périphériques\" l'inclut déjà (Tree pattern) : "
      f"{db.products.count_documents({'category.path': 'peripheriques', 'status': 'active', '_id': 'VR-000001'})} résultat")

# -----------------------------------------------------------------------------------------
titre("V2 - Nouvel attribut sur une partie des produits existants",
      "Version PCIe des cartes RTX 40 et RTX 50 : seuls ces documents changent, les autres ne bougent pas.")


def ajouter_attribut(motif, valeur):
    return db.products.update_many(
        {
            "category.id": "cartes-graphiques",
            "specs": {"$elemMatch": {"k": "chipset", "v": {"$regex": motif}}},
            "specs.k": {"$ne": "pcie_version"},                 # idempotent
        },
        {"$push": {"specs": {"k": "pcie_version", "v": valeur}}, "$set": {"updatedAt": maintenant()}},
    ).modified_count


n50 = ajouter_attribut("^GeForce RTX 50", "5.0")
n40 = ajouter_attribut("^GeForce RTX 40", "4.0")
db.categories.update_one(
    {"_id": "cartes-graphiques", "specTemplate.k": {"$ne": "pcie_version"}},
    {"$push": {"specTemplate": {"k": "pcie_version", "label": "Version PCIe", "unit": None,
                                "filterable": True, "derived": True}}},
)
print(f"   Attribut ajouté : {n50} cartes RTX 50 (PCIe 5.0), {n40} cartes RTX 40 (PCIe 4.0) lors de cette exécution")
print(f"   Cartes graphiques avec l'attribut : "
      f"{db.products.count_documents({'category.id': 'cartes-graphiques', 'specs.k': 'pcie_version'})}"
      f" / {db.products.count_documents({'category.id': 'cartes-graphiques'})}")
tableau([{"sku": x["_id"], "produit": x["name"], "prix": str(x["price"]["amount"])} for x in db.products.find(
    {"category.id": "cartes-graphiques", "status": "active", "specs": {"$elemMatch": {"k": "pcie_version", "v": "5.0"}}},
    {"name": 1, "price": 1},
).sort("price.amount", 1).limit(3)], ["sku", "produit", "prix"])

# -----------------------------------------------------------------------------------------
titre("V3 - Changement de STRUCTURE : Schema Versioning pattern",
      "La v2 ajoute l'éco-participation au prix. v1 et v2 cohabitent ; migration au fil des écritures.")
# 1. Le validateur accepte les deux versions, mais exige l'éco-participation en v2
options = next(db.list_collections(filter={"name": "products"}))["options"]
base = options["validator"] if "$jsonSchema" in options["validator"] else options["validator"]["$and"][0]
regle_v2 = {"$or": [{"schemaVersion": {"$lt": 2}}, {"price": None}, {"price.ecoParticipation": {"$type": "decimal"}}]}
db.command("collMod", "products", validator={"$and": [base, regle_v2]}, validationLevel="moderate")
print("   Validateur enrichi : schemaVersion 2 => price.ecoParticipation obligatoire")

# 2. Migration paresseuse : un document passe en v2 quand on le réécrit
essayer("MON-000351 passe en v2 avec son éco-participation", lambda: db.products.update_one(
    {"_id": "MON-000351"},
    {"$set": {"schemaVersion": 2, "price.ecoParticipation": Decimal128("0.50"), "updatedAt": maintenant()}}))
essayer("MON-000182 passe en v2 SANS éco-participation", lambda: db.products.update_one(
    {"_id": "MON-000182"}, {"$set": {"schemaVersion": 2}}))

# 3. La lecture gère les deux versions
print("\n   Lecture compatible v1/v2 (éco-participation à 0 si absente) :")
tableau([{"sku": x["_id"], "version": x["schemaVersion"], "produit": x["name"], "prix": str(x["prix"]),
          "dont éco-part.": str(x["eco"])} for x in db.products.aggregate([
              {"$match": {"_id": {"$in": ["MON-000351", "MON-000182"]}}},
              {"$project": {"name": 1, "schemaVersion": 1, "prix": "$price.amount",
                            "eco": {"$ifNull": ["$price.ecoParticipation", Decimal128("0")]}}},
          ])], ["sku", "version", "produit", "prix", "dont éco-part."])
print("\n   Répartition des versions de schéma :")
for v in db.products.aggregate([{"$group": {"_id": "$schemaVersion", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}]):
    print(f"     schemaVersion {v['_id']} : {v['n']} documents")

# -----------------------------------------------------------------------------------------
titre("V4 - Garde-fous : flexible ne veut pas dire sans contrôle",
      "Le validateur bloque ce qui casserait l'application ou les patterns.")


def fiche_minimale(id_):
    return {
        "_id": id_, "schemaVersion": 1, "type": "physical", "name": "Produit test", "slug": "produit-test-" + id_.lower(),
        "brand": {"id": "test", "name": "Test"}, "category": {"id": "souris", "name": "Souris", "path": ["peripheriques", "souris"]},
        "status": "draft", "price": None, "specs": [], "createdAt": maintenant(),
    }


def sans_nom():
    f = fiche_minimale("TEST-000001")
    del f["name"]
    db.products.insert_one(f)


essayer("fiche sans nom", sans_nom)
essayer("SKU au mauvais format (test-1)", lambda: db.products.insert_one({**fiche_minimale("TEST-000002"), "_id": "test-1"}))
essayer("6 avis embarqués (Subset limité à 5)", lambda: db.products.insert_one({
    **fiche_minimale("TEST-000003"), "recentReviews": [{"title": f"avis {i}"} for i in range(1, 7)],
}))
essayer("avis noté 6/5", lambda: db.reviews.insert_one({
    "productId": "MB-000131", "customer": {"id": "CUST-000001", "displayName": "Test"}, "rating": 6,
    "status": "pending", "createdAt": maintenant(),
}))
essayer("fiche en brouillon complète, avec un attribut jamais vu", lambda: db.products.insert_one({
    **fiche_minimale("TEST-000004"), "specs": [{"k": "attribut_inedit", "v": "accepté sans migration"}],
}))
db.products.delete_many({"_id": {"$regex": "^TEST-"}})   # nettoyage
