"""
Lot 1 — 03 : avis clients SYNTHÉTIQUES + résumé embarqué dans les produits

Le jeu public ne contient pas d'avis : on les génère pour démontrer le modèle.
 - Générateur déterministe (graine fixe) : mêmes données à chaque chargement.
 - Seuls les produits actifs (vendus) reçoivent des avis.
 - Distribution très inégale, comme en réalité : beaucoup de produits avec 0 à
   quelques avis, et quelques best-sellers avec des centaines d'avis. C'est ce
   cas qui interdit d'embarquer TOUS les avis dans la fiche produit.
 - Chaque avis porte `synthetic: true`.
Ensuite, le résumé (Computed pattern) et les 5 derniers avis (Subset pattern) sont
calculés depuis `reviews` (la source de vérité) et fusionnés ($merge) dans `products`.
"""
import math
import sys
import time

sys.path.insert(0, "/lot1/lib")
from catalogue_config import CATEGORIES, majuscule  # noqa: E402
from outils import arrondi, connecter, date_ms  # noqa: E402
from pipelines import nom_affiche, resume_avis  # noqa: E402

db = connecter()
debut = time.time()
print("[03] Génération des avis synthétiques")


# --- Générateur pseudo-aléatoire déterministe (mulberry32, entiers sur 32 bits) ------
def mulberry32(graine):
    etat = graine & 0xFFFFFFFF

    def imul(x, y):
        return (x * y) & 0xFFFFFFFF

    def suivant():
        nonlocal etat
        etat = (etat + 0x6D2B79F5) & 0xFFFFFFFF
        t = imul(etat ^ (etat >> 15), 1 | etat)
        t = ((t + imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
    return suivant


alea = mulberry32(20261117)


def choix(tab):
    return tab[math.floor(alea() * len(tab))]


def gauss():
    # Box-Muller ; les deux tirages sont faits dans cet ordre
    u = alea() or 1e-9
    return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * alea())


# --- Textes ------------------------------------------------------------------------------
TITRES = {
    5: ["Excellent produit", "Parfait, je recommande", "Au top", "Rien à redire", "Très satisfait"],
    4: ["Très bon rapport qualité/prix", "Bon produit", "Satisfait de mon achat", "Bon choix"],
    3: ["Correct sans plus", "Moyen", "Fait le travail", "Avis mitigé"],
    2: ["Déçu", "En dessous de mes attentes", "Bof"],
    1: ["À éviter", "Très déçu", "Produit défectueux"],
}
PHRASES = {
    5: ["{N} fonctionne parfaitement depuis plusieurs semaines.", "Les performances sont au rendez-vous.",
        "Finitions irréprochables, rien à signaler.", "Je recommande sans hésiter."],
    4: ["{N} remplit bien son rôle.", "Bonnes performances pour le prix.",
        "Quelques détails perfectibles, rien de grave.", "Installation simple."],
    3: ["{N} fait ce qu'on lui demande, sans plus.", "Performances correctes mais pas exceptionnelles.",
        "La notice pourrait être plus claire.", "Rapport qualité/prix moyen."],
    2: ["{N} a montré des faiblesses assez vite.", "Les performances ne sont pas à la hauteur.",
        "Finitions décevantes.", "Je ne le rachèterais pas."],
    1: ["{N} a cessé de fonctionner au bout de quelques jours.", "Retour au SAV dès la première semaine.",
        "Ne correspond pas à la description.", "Très mauvaise expérience."],
}
COMMUNES = ["Livraison rapide et emballage soigné.", "Reçu en 48 h.", "Bon suivi de commande.", ""]
NOM_COMMUN = {c["_id"]: c["noun"] for c in CATEGORIES if c.get("noun")}

DATE_MIN = 1672531200000        # 2023-01-01T00:00:00Z, en millisecondes
DATE_MAX = 1753228800000        # 2025-07-23T00:00:00Z : date de mise à jour du jeu public

# --- Génération --------------------------------------------------------------------------
produits = list(db.products.find({"status": "active"}, {"_id": 1, "category.id": 1}).sort("_id", 1))

lot = []
total = best_sellers = 0


def vider():
    global lot, total
    if lot:
        db.reviews.insert_many(lot, ordered=False)
        total += len(lot)
        lot = []


for index, p in enumerate(produits):
    if index % 600 == 0:                                   # best-sellers
        n = 300 + math.floor(alea() * 900)
        best_sellers += 1
    else:
        n = 0 if alea() < 0.3 else 1 + math.floor(30 * alea() ** 4)

    qualite = 3.0 + alea() * 1.8                           # « vraie » note moyenne du produit
    nom = majuscule(NOM_COMMUN.get(p["category"]["id"]) or "ce produit")

    for _ in range(n):
        note = min(5, max(1, arrondi(qualite + gauss() * 0.9)))
        num_client = math.floor(alea() * 25000) + 1
        morceaux = [choix(PHRASES[note]), choix(PHRASES[note]), choix(COMMUNES)]
        corps = " ".join(s for j, s in enumerate(morceaux) if s and morceaux.index(s) == j).replace("{N}", nom, 1)
        tirage = alea()
        lot.append({
            "productId": p["_id"],                         # Référence (pas d'embarquement complet)
            "customer": {                                  # Extended Reference : seul le nom affiché est recopié
                "id": f"CUST-{num_client:06d}",
                "displayName": nom_affiche(num_client),
            },
            "rating": note,
            "title": choix(TITRES[note]),
            "body": corps,
            "status": "approved" if tirage < 0.95 else ("pending" if tirage < 0.99 else "rejected"),
            "verifiedPurchase": alea() < 0.8,
            "helpfulVotes": math.floor(alea() ** 3 * 50),
            "synthetic": True,
            "createdAt": date_ms(DATE_MIN + math.floor(alea() * (DATE_MAX - DATE_MIN))),
        })
        if len(lot) >= 10000:
            vider()
vider()
print(f"  {total} avis générés pour {len(produits)} produits actifs (dont {best_sellers} best-sellers)")

# --- Résumé embarqué : Computed pattern + Subset pattern ----------------------------------
# Calculé depuis la source de vérité (reviews, statut "approved" uniquement), puis
# fusionné dans products. Ce même pipeline sert de job de réconciliation.
list(db.reviews.aggregate(resume_avis(), allowDiskUse=True))

notes = db.products.count_documents({"rating.count": {"$gt": 0}})
print(f"[03] OK : résumé des avis fusionné dans {notes} produits en {time.time() - debut:.1f} s")
