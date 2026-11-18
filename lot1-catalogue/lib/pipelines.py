"""
Lot 1 — Pipelines et transformations partagés (chargement initial + écritures)

Le résumé des avis embarqué dans chaque produit est une DONNÉE DÉRIVÉE :
  rating        = {count, sum, average, distribution}   (Computed pattern)
  recentReviews = les 5 derniers avis approuvés, tronqués (Subset pattern)
La source de vérité reste la collection `reviews`. Le même pipeline sert :
  - au chargement initial (init/03-avis-synthetiques.py),
  - au job de réconciliation (recalcul complet, idempotent),
  - à la réconciliation d'un seul produit.
"""
TAILLE_EXTRAIT = 200
NB_AVIS_EMBARQUES = 5

# Nom affiché des clients des avis synthétiques : un prénom et une initiale, déduits du numéro client
PRENOMS = ["Julien", "Camille", "Thomas", "Léa", "Nicolas", "Manon", "Maxime", "Chloé", "Antoine", "Sarah",
           "Hugo", "Emma", "Lucas", "Inès", "Alexandre", "Clara", "Romain", "Laura", "Kevin", "Marie", "Yanis", "Sofia",
           "Mathieu", "Pauline", "Karim", "Elise", "Florian", "Amandine", "Samuel", "Nora"]
LETTRES = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def nom_affiche(numero_client):
    """« Sofia J. » pour CUST-024081 : la même règle à la génération et à la remise en état."""
    return PRENOMS[numero_client % len(PRENOMS)] + " " + LETTRES[(numero_client * 7) % 26] + "."


def avis_embarque(a):
    """Forme d'un avis embarqué dans la fiche produit (côté application, pour les écritures)."""
    return {
        "reviewId": a["_id"], "rating": a["rating"], "title": a["title"],
        "excerpt": (a.get("body") or "")[:TAILLE_EXTRAIT],
        "customer": a["customer"]["displayName"], "verifiedPurchase": a["verifiedPurchase"],
        "createdAt": a["createdAt"],
    }


def resume_avis(filtre=None):
    """Pipeline : recalcule le résumé depuis `reviews` et le fusionne dans `products`."""
    def par_note(n):
        return {"$sum": {"$cond": [{"$eq": ["$rating", n]}, 1, 0]}}
    return [
        {"$match": {"status": "approved", **(filtre or {})}},
        {
            "$group": {
                "_id": "$productId",
                "count": {"$sum": 1},
                "sum": {"$sum": "$rating"},
                "n1": par_note(1), "n2": par_note(2), "n3": par_note(3), "n4": par_note(4), "n5": par_note(5),
                "recent": {
                    "$topN": {
                        "n": NB_AVIS_EMBARQUES,
                        "sortBy": {"createdAt": -1},
                        "output": {
                            "reviewId": "$_id", "rating": "$rating", "title": "$title",
                            "excerpt": {"$substrCP": ["$body", 0, TAILLE_EXTRAIT]},
                            "customer": "$customer.displayName", "verifiedPurchase": "$verifiedPurchase",
                            "createdAt": "$createdAt",
                        },
                    },
                },
            },
        },
        {
            "$project": {
                "rating": {
                    "count": "$count",
                    "sum": "$sum",
                    "average": {"$round": [{"$divide": ["$sum", "$count"]}, 2]},
                    "distribution": {"1": "$n1", "2": "$n2", "3": "$n3", "4": "$n4", "5": "$n5"},
                },
                "recentReviews": "$recent",
            },
        },
        {"$merge": {"into": "products", "on": "_id", "whenMatched": "merge", "whenNotMatched": "discard"}},
    ]


def resume_vide():
    return {"average": None, "count": 0, "sum": 0, "distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}}


def reconcilier_produit(db, product_id):
    """Réconciliation d'un seul produit : remise à zéro, puis recalcul depuis la source de vérité."""
    db.products.update_one({"_id": product_id}, {"$set": {"rating": resume_vide(), "recentReviews": []}})
    list(db.reviews.aggregate(resume_avis({"productId": product_id})))
