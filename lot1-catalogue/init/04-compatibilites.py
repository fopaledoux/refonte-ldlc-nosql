"""
Lot 1 — 04 : liste courte de produits compatibles, embarquée dans la fiche

Frontière avec le Lot 4 :
 - Le Lot 4 (graphe) est la source de vérité des relations entre produits et sait
   faire les parcours (« compatibles avec ce boîtier ET cette carte mère »).
 - La fiche produit n'embarque qu'une VITRINE : au plus 10 produits compatibles,
   précalculés, pour afficher le bloc « produits compatibles » sans requête de plus.
   Chaque entrée est une Extended Reference (_id + champs affichés, sans le prix,
   qui change souvent).
Règles utilisées ici (à remplacer par le Lot 4) :
 - processeur <-> carte mère : même socket (déduit pour les processeurs)
 - carte mère <-> boîtier    : format de carte mère accepté par le boîtier
On garde les produits actifs les mieux notés.
"""
import sys
import time

sys.path.insert(0, "/lot1/lib")
from pymongo import UpdateOne  # noqa: E402

from outils import connecter  # noqa: E402

db = connecter()
debut = time.time()
print("[04] Calcul des listes de produits compatibles")


def valeurs(p, k):
    return [s["v"] for s in p["specs"] if s["k"] == k]


def charger(categorie):
    produits = list(db.products.find({"category.id": categorie, "status": "active"},
                                     {"name": 1, "slug": 1, "category.id": 1, "specs": 1, "rating": 1})
                    .sort("rating.count", -1))                    # pré-tri : les plus évalués d'abord
    # Tri stable : meilleure note d'abord, puis nombre d'avis
    return sorted(produits, key=lambda p: (-(p["rating"].get("average") or 0), -p["rating"]["count"]))


processeurs = charger("processeurs")
cartes_meres = charger("cartes-meres")
boitiers = charger("boitiers")


def ref(p, raison):
    return {"_id": p["_id"], "name": p["name"], "slug": p["slug"], "category": p["category"]["id"], "reason": raison}


def regrouper(produits, k):
    index = {}
    for p in produits:
        for v in valeurs(p, k):
            index.setdefault(v, []).append(p)
    return index


cm_par_socket = regrouper(cartes_meres, "socket")
cpu_par_socket = regrouper(processeurs, "socket")
boitiers_par_format = regrouper(boitiers, "mb_form_factor")
cm_par_format = regrouper(cartes_meres, "form_factor")

operations = []


def ecrire(p, liste):
    if liste:
        operations.append(UpdateOne({"_id": p["_id"]}, {"$set": {"compatibleProducts": liste[:10]}}))


for cpu in processeurs:
    socket = next(iter(valeurs(cpu, "socket")), None)
    if socket:
        ecrire(cpu, [ref(m, f"Socket {socket}") for m in cm_par_socket.get(socket, [])[:5]])
for cm in cartes_meres:
    socket = next(iter(valeurs(cm, "socket")), None)
    format_carte = next(iter(valeurs(cm, "form_factor")), None)
    ecrire(cm, [ref(c, f"Socket {socket}") for c in cpu_par_socket.get(socket, [])[:5]]
           + [ref(b, f"Format {format_carte}") for b in boitiers_par_format.get(format_carte, [])[:5]])
for boitier in boitiers:
    vus, liste = set(), []
    for f in valeurs(boitier, "mb_form_factor"):
        for cm in cm_par_format.get(f, []):
            if len(liste) < 5 and cm["_id"] not in vus:
                vus.add(cm["_id"])
                liste.append(ref(cm, f"Format {f}"))
    ecrire(boitier, liste)

if operations:
    db.products.bulk_write(operations, ordered=False)
print(f"[04] OK : {len(operations)} fiches enrichies en {time.time() - debut:.1f} s")
