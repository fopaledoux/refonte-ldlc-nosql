"""
Lot 4 -> Lot 1 : la liste courte compatibleProducts de chaque fiche, calculée dans le
graphe, puis écrite dans MongoDB (10 produits au plus par fiche, 5 au plus par catégorie).

Le Lot 1 l'avait calculée une fois, à son chargement : même socket, format accepté par le
boîtier, classement par note. Le graphe ajoute le type de mémoire (barrettes et cartes
mères) et classe chaque produit compatible d'abord par les paniers réels (BOUGHT_WITH),
puis par sa popularité (commandes). En production : un job nocturne, après le recalcul
des arêtes BOUGHT_WITH.

Le graphe fournit les SKU et la raison de la compatibilité ; le nom et le slug affichés
sont relus dans le catalogue, qui en reste la source de vérité.

Comme les écritures de démonstration du Lot 1, le résultat reste dans la base :
  docker compose run --rm lot4-jobs python /lot4/jobs/compatibles_vers_lot1.py
Avant sa première écriture, le job copie les listes du Lot 1 dans la collection
compatibleProductsBackup. Pour les remettre à l'identique (et supprimer la copie) :
  docker compose run --rm lot4-jobs python /lot4/jobs/compatibles_vers_lot1.py --annuler
"""
import sys
import time
from collections import Counter

sys.path.insert(0, "/lot1/lib")
sys.path.insert(0, "/lot4/lib")
from pymongo import UpdateOne  # noqa: E402

from graphe import attendre, cypher  # noqa: E402
from outils import connecter, tableau, titre  # noqa: E402

EXEMPLES = ["CPU-000054", "MB-000227", "RAM-001835"]
SAUVEGARDE = "compatibleProductsBackup"
RAISON = {"socket": "Socket", "memory": "Mémoire", "form_factor": "Format"}

# Pour chaque processeur, carte mère, barrette et boîtier : les produits d'une autre catégorie
# reliés par une interface (l'un l'exige, l'autre la fournit), 5 au plus par catégorie, 10 en tout.
COMPATIBLES = """
MATCH (p:Product) WHERE p:CPU OR p:Motherboard OR p:Memory OR p:Case
CALL {
  WITH p
  MATCH (p)-[r1:REQUIRES|PROVIDES]->(i:Interface)<-[r2:REQUIRES|PROVIDES]-(c:Product)
  WHERE type(r1) <> type(r2) AND c.category <> p.category
  OPTIONAL MATCH (p)-[b:BOUGHT_WITH]->(c)
  WITH c, i, coalesce(b.score, 0.0) AS score
  ORDER BY score DESC, c.orders DESC, c.sku
  WITH c.category AS categorie, collect({sku: c.sku, category: c.category, kind: i.kind,
                                         interface: i.name, score: score, orders: c.orders})[..5] AS meilleurs
  UNWIND meilleurs AS m
  RETURN m ORDER BY m.score DESC, m.orders DESC, m.sku LIMIT 10
}
RETURN p.sku AS sku, collect(m) AS compatibles
"""

db = connecter()


def afficher(sku, liste, entete):
    print(f"\n   {sku} {entete} :")
    tableau([{"sku": c["_id"], "catégorie": c["category"], "raison": c["reason"], "nom": c["name"]} for c in liste],
            ["sku", "catégorie", "raison", "nom"])


def avant():
    return {p["_id"]: p.get("compatibleProducts") or []
            for p in db.products.find({"_id": {"$in": EXEMPLES}}, {"compatibleProducts": 1})}


def sauvegarder():
    """Avant la première écriture du job : les listes du Lot 1, telles quelles ($out)."""
    if SAUVEGARDE in db.list_collection_names():
        return 0
    db.products.aggregate([{"$match": {"compatibleProducts": {"$exists": True}}},
                           {"$project": {"compatibleProducts": 1}}, {"$out": SAUVEGARDE}])
    return db[SAUVEGARDE].estimated_document_count()


def annuler():
    """Remet les listes d'avant le premier passage du job, puis supprime la copie."""
    titre("Retour aux listes calculées par le Lot 1", f"copie : collection {SAUVEGARDE}")
    if SAUVEGARDE not in db.list_collection_names():
        print("   Aucune copie : le job n'a rien écrit depuis le dernier retour en arrière.")
        return
    avant_lot4 = {d["_id"]: d["compatibleProducts"] for d in db[SAUVEGARDE].find()}
    operations = [UpdateOne({"_id": p["_id"]}, {"$unset": {"compatibleProducts": ""}})
                  for p in db.products.find({"compatibleProducts": {"$exists": True}}, {"_id": 1})
                  if p["_id"] not in avant_lot4]
    retirees = len(operations)
    operations += [UpdateOne({"_id": sku}, {"$set": {"compatibleProducts": liste}}) for sku, liste in avant_lot4.items()]
    r = db.products.bulk_write(operations, ordered=False)
    db[SAUVEGARDE].drop()
    print(f"   {len(avant_lot4)} listes remises, {retirees} listes ajoutées par le Lot 4 retirées "
          f"({r.modified_count} fiches modifiées) ; copie supprimée")


if __name__ == "__main__":
    if "--annuler" in sys.argv:
        annuler()
        sys.exit(0)

    titre("Lot 4 -> Lot 1 : compatibleProducts calculé dans le graphe",
          "processeurs, cartes mères, barrettes et boîtiers ; 10 produits au plus par fiche")
    attendre()
    copies = sauvegarder()
    if copies:
        print(f"   Premier passage : {copies} listes du Lot 1 copiées dans {SAUVEGARDE} (pour --annuler)")
    anciens = avant()
    debut = time.time()
    lignes = cypher(COMPATIBLES)
    duree_graphe = time.time() - debut
    print(f"   Neo4j : {len(lignes)} fiches calculées en {duree_graphe:.1f} s")

    # Nom et slug : lus dans le catalogue (source de vérité), en une requête $in
    skus = {c["sku"] for l in lignes for c in l["compatibles"]}
    fiches = {p["_id"]: p for p in db.products.find({"_id": {"$in": list(skus)}}, {"name": 1, "slug": 1})}
    operations, raisons = [], Counter()
    for l in lignes:
        liste = []
        for c in l["compatibles"]:
            if c["sku"] in fiches:
                raison = f"{RAISON[c['kind']]} {c['interface']}"
                raisons[RAISON[c["kind"]]] += 1
                liste.append({"_id": c["sku"], "name": fiches[c["sku"]]["name"], "slug": fiches[c["sku"]]["slug"],
                              "category": c["category"], "reason": raison})
        if liste:
            operations.append(UpdateOne({"_id": l["sku"]}, {"$set": {"compatibleProducts": liste}}))
    r = db.products.bulk_write(operations, ordered=False)
    print(f"   MongoDB : {r.matched_count} fiches trouvées, {r.modified_count} modifiées "
          f"(écriture groupée, {time.time() - debut - duree_graphe:.1f} s)")
    print("   Raisons : " + ", ".join(f"{k} {v}" for k, v in raisons.most_common()))

    nouveaux = avant()
    for sku in EXEMPLES:
        afficher(sku, anciens.get(sku, []), "avant (Lot 1 : même socket ou même format, classé par note)")
        afficher(sku, nouveaux.get(sku, []), "après (Lot 4 : interfaces du graphe, classé par paniers réels)")
    print("\n   Les fiches gardent ces listes. Pour revenir à celles du Lot 1 :")
    print("   docker compose run --rm lot4-jobs python /lot4/jobs/compatibles_vers_lot1.py --annuler")
