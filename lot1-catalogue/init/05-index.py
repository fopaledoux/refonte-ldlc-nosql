"""
Lot 1 — 05 : index (créés APRÈS le chargement en masse, plus rapide)

Chaque index répond à une requête type identifiée (Query-Driven) et suit la règle
ESR : champs en Égalité, puis champs de Tri (Sort), puis de Plage (Range).
Les index de listing sont PARTIELS : ils ne contiennent que les produits actifs
(~24 % du catalogue), donc plus petits et plus souvent en mémoire.
"""
import sys

sys.path.insert(0, "/lot1/lib")
from outils import connecter  # noqa: E402

db = connecter()
print("[05] Création des index")
ACTIFS = {"partialFilterExpression": {"status": "active"}}

# R1 — page produit par URL
db.products.create_index([("slug", 1)], name="ux_slug", unique=True)

# R2 — listing d'une catégorie (et de ses sous-catégories), trié par prix, paginé
db.products.create_index([("category.path", 1), ("price.amount", 1), ("_id", 1)], name="ix_listing_prix", **ACTIFS)

# R8 — listing trié par note
db.products.create_index([("category.path", 1), ("rating.average", -1), ("_id", 1)], name="ix_listing_note", **ACTIFS)

# R3/R4 — filtres à facettes sur des attributs variables (Attribute pattern) :
# un seul index sert TOUS les attributs de TOUTES les catégories.
db.products.create_index([("category.id", 1), ("specs.k", 1), ("specs.v", 1)], name="ix_facettes", **ACTIFS)

# R7 — produits d'une marque dans une catégorie, triés par prix.
# (Version initiale {brand.id, category.id} : explain() montrait 1 256 documents lus
#  pour 5 renvoyés + un tri en mémoire. Le tri est maintenant porté par l'index.)
db.products.create_index([("brand.id", 1), ("category.id", 1), ("price.amount", 1)], name="ix_marque", **ACTIFS)

# R6 — recherche plein texte (sans racinisation : noms de modèles, pas de la prose)
db.products.create_index([("name", "text"), ("brand.name", "text")], name="tx_recherche",
                         weights={"name": 10, "brand.name": 5}, default_language="none")

# E4 — retrouver les fiches qui recopient un produit (mise à jour des Extended References)
db.products.create_index([("compatibleProducts._id", 1)], name="ix_compatibles_inverse")

# R5 — avis d'un produit, du plus récent au plus ancien
db.reviews.create_index([("productId", 1), ("status", 1), ("createdAt", -1)], name="ix_avis_produit")
# « Mes avis » dans l'espace client
db.reviews.create_index([("customer.id", 1), ("createdAt", -1)], name="ix_avis_client")
# File de modération : uniquement les avis en attente
db.reviews.create_index([("createdAt", 1)], name="ix_moderation", partialFilterExpression={"status": "pending"})

# Navigation dans l'arborescence
db.categories.create_index([("parent", 1)], name="ix_parent")
db.categories.create_index([("path", 1)], name="ix_path")

for c in ["products", "reviews", "categories"]:
    print(f"  {c:<11} {', '.join(i['name'] for i in db[c].list_indexes())}")
print("[05] OK")
