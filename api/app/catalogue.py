"""
Routes du catalogue (MongoDB, Lot 1) : fiche, listing avec filtres, facettes, recherche, avis.

Chaque route reprend une requête type du Lot 1 et utilise l'index conçu pour elle
(ux_slug, ix_listing_prix, ix_listing_note, ix_facettes, tx_recherche, ix_avis_produit).
"""
import re

from bson import Decimal128
from fastapi import APIRouter, Header, HTTPException, Query

from . import analytics, session
from .bases import mongo, propre

router = APIRouter(tags=["Catalogue (MongoDB, Lot 1)"])
PROJ_LISTING = {"name": 1, "slug": 1, "brand.name": 1, "price": 1, "rating.average": 1, "rating.count": 1}


def ligne(p):
    return {"sku": p["_id"], "name": p["name"], "slug": p.get("slug"), "brand": (p.get("brand") or {}).get("name"),
            "price": (p.get("price") or {}).get("amount"), "currency": (p.get("price") or {}).get("currency"),
            "rating": (p.get("rating") or {}).get("average"), "reviews": (p.get("rating") or {}).get("count")}


def valeur(texte):
    """« 8 » -> 8, « 4.5 » -> 4.5, « true » -> True, sinon le texte tel quel."""
    if re.fullmatch(r"-?\d+", texte):
        return int(texte)
    if re.fullmatch(r"-?\d+\.\d+", texte):
        return float(texte)
    return {"true": True, "false": False}.get(texte, texte)


def filtre_specs(specs):
    """spec=socket:AM5, spec=core_count>=8, spec=tdp<=120 -> $all de $elemMatch (Attribute pattern)."""
    conditions = []
    for s in specs:
        m = re.fullmatch(r"([a-z0-9_]+)(>=|<=|:)(.+)", s)
        if not m:
            raise HTTPException(422, f"Filtre invalide : {s} (formes acceptées : k:v, k>=v, k<=v)")
        k, op, v = m.group(1), m.group(2), valeur(m.group(3))
        condition = v if op == ":" else {"$gte" if op == ">=" else "$lte": v}
        conditions.append({"$elemMatch": {"k": k, "v": condition}})
    return {"specs": {"$all": conditions}} if conditions else {}


@router.get("/categories", summary="Arbre des catégories (Tree pattern)")
def categories():
    return propre([{"id": c["_id"], "name": c["name"], "parent": c.get("parent"), "path": c["path"],
                    "products": c.get("productCount")}
                   for c in mongo().categories.find({}, {"specTemplate": 0}).sort("path", 1)])


@router.get("/categories/{category_id}/products",
            summary="Produits en vente d'une catégorie et de ses sous-catégories, filtrés et paginés (R2, R3, R8)")
def listing(category_id: str,
            sort: str = Query("price", pattern="^(price|rating)$"),
            spec: list[str] = Query([], description="Filtre sur une caractéristique : socket:AM5, core_count>=8, tdp<=120"),
            max_price: str | None = Query(None, description="Prix maximal, ex. 500"),
            limit: int = Query(10, ge=1, le=50),
            after: str | None = Query(None, description="Curseur de la page suivante (champ next de la réponse)")):
    filtre = {"category.path": category_id, "status": "active", **filtre_specs(spec)}
    if max_price:
        filtre["price.amount"] = {"$lte": Decimal128(max_price)}
    if sort == "price":
        tri = [("price.amount", 1), ("_id", 1)]
        if after:                                              # pagination par curseur (keyset), sans skip()
            prix, sku = after.split("|")
            filtre["$or"] = [{"price.amount": {"$gt": Decimal128(prix)}},
                             {"price.amount": Decimal128(prix), "_id": {"$gt": sku}}]
    else:
        filtre["rating.count"] = {"$gte": 1}
        tri = [("rating.average", -1), ("_id", 1)]
        if after:
            note, sku = after.split("|")
            filtre["$or"] = [{"rating.average": {"$lt": float(note)}}, {"rating.average": float(note), "_id": {"$gt": sku}}]
    produits = list(mongo().products.find(filtre, PROJ_LISTING).sort(tri).limit(limit))
    suivant = None
    if len(produits) == limit:
        d = produits[-1]
        suivant = f"{d['price']['amount'] if sort == 'price' else d['rating']['average']}|{d['_id']}"
    return propre({"category": category_id, "sort": sort, "items": [ligne(p) for p in produits], "next": suivant})


@router.get("/categories/{category_id}/facets", summary="Nombre de produits par valeur de filtre (R4)")
def facettes(category_id: str, top: int = Query(6, ge=1, le=20)):
    categorie = mongo().categories.find_one({"_id": category_id})
    if not categorie:
        raise HTTPException(404, f"Catégorie inconnue : {category_id}")
    gabarit = {s["k"]: s for s in categorie.get("specTemplate", []) if s.get("filterable")}
    resultat = mongo().products.aggregate([
        {"$match": {"category.id": category_id, "status": "active"}},
        {"$unwind": "$specs"},
        {"$match": {"specs.k": {"$in": list(gabarit)}}},
        {"$group": {"_id": {"k": "$specs.k", "v": "$specs.v"}, "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$group": {"_id": "$_id.k", "values": {"$push": {"value": "$_id.v", "count": "$n"}}}},
        {"$project": {"values": {"$slice": ["$values", top]}}},
        {"$sort": {"_id": 1}},
    ])
    return propre({"category": category_id, "facets": [
        {"key": f["_id"], "label": gabarit[f["_id"]]["label"], "unit": gabarit[f["_id"]].get("unit"),
         "values": f["values"]} for f in resultat]})


@router.get("/search", summary="Recherche plein texte dans les produits en vente (R6)")
def recherche(q: str = Query(..., min_length=2), limit: int = Query(10, ge=1, le=50)):
    produits = mongo().products.find({"$text": {"$search": q}, "status": "active"},
                                     {"score": {"$meta": "textScore"}, **PROJ_LISTING}) \
        .sort([("score", {"$meta": "textScore"}), ("price.amount", 1)]).limit(limit)
    return propre({"query": q, "items": [ligne(p) for p in produits]})


@router.get("/products/{slug}", summary="Fiche produit complète en une lecture (R1) ; la vue est enregistrée (Lot 3)")
def fiche(slug: str, x_session_id: str | None = Header(None, description="Session (facultative) : POST /sessions")):
    p = mongo().products.find_one({"slug": slug}, {"source": 0})
    if not p:
        raise HTTPException(404, f"Produit inconnu : {slug}")
    s = session.lire(x_session_id) if x_session_id else None    # session facultative, prolongée si elle existe
    analytics.enregistrer_vue(p["_id"], s)
    return propre({**{k: v for k, v in p.items() if k not in ("createdAt", "updatedAt")},
                   "views_today": analytics.vues_du_jour(p["_id"])["views"]})


@router.get("/products/{sku}/reviews", summary="Avis approuvés au-delà des 5 embarqués dans la fiche (R5)")
def avis(sku: str, skip: int = Query(5, ge=0), limit: int = Query(5, ge=1, le=50)):
    curseur = mongo().reviews.find({"productId": sku, "status": "approved"},
                                   {"rating": 1, "title": 1, "body": 1, "customer.displayName": 1, "createdAt": 1,
                                    "helpfulVotes": 1}).sort("createdAt", -1).skip(skip).limit(limit)
    return propre({"sku": sku, "items": [{"id": a["_id"], "rating": a["rating"], "title": a["title"], "body": a.get("body"),
                                          "author": a["customer"]["displayName"], "helpful_votes": a.get("helpfulVotes"),
                                          "created_at": a["createdAt"]} for a in curseur]})
