"""
Lot 1 — 02 : import du jeu public (PC Part Dataset) vers le modèle document

Pour chaque ligne source : un document `products` autonome, qui contient tout ce
que la page produit affiche (« les données lues ensemble sont stockées
ensemble »). Les catégories et les marques sont aussi dans leurs propres
collections ; le produit n'en recopie que les champs affichés (Extended Reference).
"""
import json
import os
import sys
import time

sys.path.insert(0, "/lot1/lib")
from bson import Decimal128  # noqa: E402

from catalogue_config import CATEGORIES, construire_specs, extraire_marque, est_nombre, slugify  # noqa: E402
from outils import connecter, decimales, maintenant, normaliser  # noqa: E402

db = connecter()
LOT1 = os.environ.get("LOT1_DIR", "/lot1")
DOSSIER = LOT1 + "/data/raw/pc-part-dataset/json"
instant = maintenant()
debut = time.time()

print("[02] Import du catalogue depuis " + DOSSIER)

# --- 1. Arborescence des catégories (Tree pattern) -----------------------------------
PAR_ID = {c["_id"]: c for c in CATEGORIES}


def chemin(c):
    """Ancêtres d'une catégorie, de la racine à elle-même."""
    p = []
    while c:
        p.insert(0, c["_id"])
        c = PAR_ID[c["parent"]] if c["parent"] else None
    return p


# --- 2. Produits et marques ------------------------------------------------------------
marques = {}
compte_par_categorie = {}
total = actifs = 0

for c in [x for x in CATEGORIES if x.get("source")]:
    with open(f"{DOSSIER}/{c['source']}.json", encoding="utf-8") as f:
        lignes = json.load(f)
    path = chemin(c)

    docs = []
    for i, r in enumerate(lignes):
        sku = f"{c['skuPrefix']}-{i + 1:06d}"
        nom_marque = extraire_marque(r["name"])
        id_marque = slugify(nom_marque)
        if id_marque not in marques:
            marques[id_marque] = {"_id": id_marque, "name": nom_marque, "productCount": 0,
                                  "activeProductCount": 0, "categories": set()}
        marque = marques[id_marque]
        est_actif = est_nombre(r.get("price"))
        marque["productCount"] += 1
        if est_actif:
            marque["activeProductCount"] += 1
        marque["categories"].add(c["_id"])

        doc = {
            "_id": sku,
            "schemaVersion": 1,
            "type": c.get("productType") or "physical",
            "name": r["name"],
            "slug": f"{slugify(r['name'])}-{sku.lower()}",
            "brand": {"id": id_marque, "name": marque["name"]},          # Extended Reference
            "category": {"id": c["_id"], "name": c["name"], "path": path},  # Extended Reference + ancêtres
            "status": "active" if est_actif else "discontinued",
            "price": {"amount": Decimal128(decimales(r["price"], 2)), "currency": "USD"} if est_actif else None,
            "specs": construire_specs(c, r),                             # Attribute pattern
            "rating": {"average": None, "count": 0, "sum": 0,            # Computed (rempli en 03)
                       "distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}},
            "recentReviews": [],                                         # Subset (rempli en 03)
            "compatibleProducts": [],                                    # Liste courte bornée (remplie en 04)
            "source": {"dataset": "pc-part-dataset", "file": f"{c['source']}.json", "line": i + 1},
            "createdAt": instant,
            "updatedAt": instant,
        }
        if c.get("extra"):
            doc.update(c["extra"](r))                                    # sous-document propre au type
        if est_actif:
            actifs += 1
        docs.append(normaliser(doc))

    for i in range(0, len(docs), 5000):
        db.products.insert_many(docs[i:i + 5000], ordered=False)
    compte_par_categorie[c["_id"]] = len(docs)
    total += len(docs)
    print(f"  {c['_id']:<26} {len(docs):>6} produits")

# --- 3. Documents catégories (avec gabarit d'attributs) -------------------------------
docs_categories = []
for c in CATEGORIES:
    path = chemin(c)
    docs_categories.append({
        "_id": c["_id"],
        "name": c["name"],
        "parent": c["parent"] or None,
        "path": path,
        "level": len(path),
        "isLeaf": bool(c.get("source")),
        # Libellés et unités stockés une seule fois ici, pas dans chaque produit
        "specTemplate": [{"k": s["k"], "label": s["label"], "unit": s.get("unit") or None,
                          "filterable": bool(s.get("filterable")), "derived": bool(s.get("derived"))}
                         for s in c.get("specs", [])],
        "productCount": compte_par_categorie[c["_id"]] if c.get("source") else None,
    })
db.categories.insert_many(docs_categories)

# --- 4. Marques ------------------------------------------------------------------------
db.brands.insert_many([{**m, "categories": sorted(m["categories"])} for m in marques.values()])

print(f"[02] OK : {total} produits ({actifs} actifs, {total - actifs} arrêtés), "
      f"{len(docs_categories)} catégories, {len(marques)} marques en {time.time() - debut:.1f} s")
