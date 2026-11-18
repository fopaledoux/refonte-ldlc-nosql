"""
Recommandations et compatibilités (Neo4j, Lot 4), à partir des requêtes types R1, R3, R5 et R6.

Le graphe donne des SKU, des scores et des raisons ; le nom, le slug, le prix et le statut
viennent du catalogue (MongoDB), relus en une requête $in. Le catalogue reste la source de
vérité : un produit arrêté depuis le dernier chargement du graphe n'est jamais proposé.

Si Neo4j ne répond pas, la fiche se replie sur la liste courte compatibleProducts du Lot 1 :
la recommandation se dégrade, la page s'affiche quand même (conception, 5.5).
"""
from fastapi import APIRouter, Header, HTTPException, Query

from . import session
from .bases import GrapheIndisponible, cache, graphe, mongo, propre

router = APIRouter(tags=["Recommandations (Neo4j, Lot 4)"])
RAISON = {"socket": "Socket", "memory": "Mémoire", "form_factor": "Format"}

# R1 — souvent achetés ensemble : arêtes précalculées, 20 au plus par produit
ACHETES_ENSEMBLE = """
MATCH (:Product {sku: $sku})-[r:BOUGHT_WITH]->(q:Product)
RETURN q.sku AS sku, q.category AS category, r.orders AS orders_together, r.score AS score
ORDER BY r.score DESC, q.sku LIMIT $limite
"""

# R3 — compatibles : l'un exige une interface que l'autre fournit, classés par paniers puis popularité
COMPATIBLES = """
MATCH (p:Product {sku: $sku})-[r1:REQUIRES|PROVIDES]->(i:Interface)<-[r2:REQUIRES|PROVIDES]-(c:Product)
WHERE type(r1) <> type(r2) AND c.category <> p.category AND ($categorie IS NULL OR c.category = $categorie)
OPTIONAL MATCH (p)-[b:BOUGHT_WITH]->(c)
RETURN c.sku AS sku, c.category AS category, i.kind AS kind, i.name AS interface,
       coalesce(b.score, 0.0) AS score, c.orders AS orders
ORDER BY score DESC, orders DESC, sku LIMIT $limite
"""

# R5 — compléter un panier : suggestions des articles du panier, compatibles avec TOUT le panier
COMPLETER = """
MATCH (p:Product) WHERE p.sku IN $panier
WITH collect(p) AS panier
UNWIND panier AS p
MATCH (p)-[r:BOUGHT_WITH]->(reco:Product)
WHERE NOT reco IN panier AND NOT reco.category IN [x IN panier | x.category]
WITH panier, reco, sum(r.score) AS score, collect(p.sku) AS parce_que
WHERE all(i IN [(reco)-[:REQUIRES]->(j) | j] WHERE
          any(x IN panier WHERE (x)-[:PROVIDES]->(i))
          OR none(x IN panier WHERE EXISTS { (x)-[:PROVIDES]->(:Interface {kind: i.kind}) }))
  AND all(x IN panier WHERE all(i IN [(x)-[:REQUIRES]->(j) | j] WHERE
          NOT EXISTS { (reco)-[:PROVIDES]->(:Interface {kind: i.kind}) } OR (reco)-[:PROVIDES]->(i)))
RETURN reco.sku AS sku, reco.category AS category, score, parce_que,
       [(reco)-[:REQUIRES]->(i) | i.id] AS requires, [(reco)-[:PROVIDES]->(i) | i.id] AS provides
ORDER BY score DESC, sku LIMIT $limite
"""

# R6 — recommandations personnalisées : ce que le client a acheté -BOUGHT_WITH-> ce qu'il n'a pas encore
POUR_LE_CLIENT = """
MATCH (:Customer {id: $client})-[:PLACED]->(o:Order)-[:CONTAINS]->(achat:Product)
WHERE o.status <> 'cancelled'
WITH collect(DISTINCT achat) AS achats
UNWIND achats AS p
MATCH (p)-[r:BOUGHT_WITH]->(reco:Product)
WHERE NOT reco IN achats
WITH reco, sum(r.score) AS score, collect(DISTINCT p.sku)[..2] AS parce_que
RETURN reco.sku AS sku, reco.category AS category, score, parce_que
ORDER BY score DESC, sku LIMIT $limite
"""


def fiches(skus):
    """Nom, slug, prix et statut lus dans le catalogue, en une requête."""
    return {p["_id"]: p for p in mongo().products.find({"_id": {"$in": list(skus)}},
                                                       {"name": 1, "slug": 1, "price": 1, "status": 1})}


def enrichir(lignes, limite, champs=("category", "score")):
    """Complète chaque ligne du graphe avec le catalogue ; retire les produits qui ne sont plus en vente."""
    references = {s for l in lignes for s in [l["sku"]] + list(l.get("parce_que") or [])}
    catalogue = fiches(references)
    resultat = []
    for l in lignes:
        p = catalogue.get(l["sku"])
        if not p or p.get("status") != "active":
            continue
        item = {"sku": l["sku"], "name": p["name"], "slug": p.get("slug"),
                "price": (p.get("price") or {}).get("amount")}
        item.update({k: l[k] for k in champs if k in l})
        if "score" in item:
            item["score"] = round(item["score"], 4)
        if l.get("parce_que"):
            item["because_of"] = [{"sku": s, "name": (catalogue.get(s) or {}).get("name")} for s in l["parce_que"]]
        resultat.append(item)
    return resultat[:limite]


def existe(sku):
    if not mongo().products.find_one({"_id": sku}, {"_id": 1}):
        raise HTTPException(404, f"Produit inconnu : {sku}")


@router.get("/products/{sku}/recommendations",
            summary="Souvent achetés ensemble (R1) ; repli sur la compatibilité, puis sur la liste du Lot 1")
def achetes_ensemble(sku: str, limit: int = Query(6, ge=1, le=20)):
    existe(sku)
    try:
        lignes = graphe(ACHETES_ENSEMBLE, sku=sku, limite=limit * 2)
        if lignes:
            return propre({"sku": sku, "source": "neo4j:BOUGHT_WITH",
                           "items": enrichir(lignes, limit, ("category", "orders_together", "score"))})
        # Démarrage à froid : pas encore d'historique d'achat, on propose ce qui est compatible
        lignes = graphe(COMPATIBLES, sku=sku, categorie=None, limite=limit * 2)
        if lignes:
            for l in lignes:
                l["reason"] = f"{RAISON[l['kind']]} {l['interface']}"
            return propre({"sku": sku, "source": "neo4j:compatibility",
                           "items": enrichir(lignes, limit, ("category", "reason"))})
        source, erreur = "lot1:compatibleProducts", None
    except GrapheIndisponible as e:
        source, erreur = "lot1:compatibleProducts (Neo4j indisponible)", str(e)[:120]
    # Repli : la vitrine embarquée dans la fiche par le Lot 1
    p = mongo().products.find_one({"_id": sku}, {"compatibleProducts": 1}) or {}
    lignes = [{"sku": c["_id"], "category": c.get("category"), "reason": c.get("reason")}
              for c in p.get("compatibleProducts") or []]
    reponse = {"sku": sku, "source": source, "items": enrichir(lignes, limit, ("category", "reason"))}
    if erreur:
        reponse["degraded"] = erreur
    return propre(reponse)


@router.get("/products/{sku}/compatible",
            summary="Produits compatibles par les interfaces du graphe (socket, mémoire, format) (R3)")
def compatibles(sku: str, category: str | None = Query(None, examples=["memoire-ram"]),
                limit: int = Query(10, ge=1, le=50)):
    existe(sku)
    try:
        lignes = graphe(COMPATIBLES, sku=sku, categorie=category, limite=limit * 2)
    except GrapheIndisponible as e:
        raise HTTPException(503, f"Graphe indisponible : {str(e)[:120]}")
    for l in lignes:
        l["reason"] = f"{RAISON[l['kind']]} {l['interface']}"
    return propre({"sku": sku, "category": category,
                   "items": enrichir(lignes, limit, ("category", "reason", "score", "orders"))})


@router.get("/cart/recommendations",
            summary="Compléter le panier (Redis) : suggestions compatibles avec tout le panier (R5)")
def completer_panier(limit: int = Query(6, ge=1, le=20), x_session_id: str | None = Header(None)):
    cle = session.exiger(x_session_id)["cartKey"]
    skus = list(session.en_dict(cache().fcall("cart_get", 1, cle, session.ttl_panier(cle))))
    if not skus:
        return {"cart_key": cle, "cart": [], "items": []}
    try:
        lignes = graphe(COMPLETER, panier=skus, limite=limit * 2)
    except GrapheIndisponible as e:
        raise HTTPException(503, f"Graphe indisponible : {str(e)[:120]}")
    return propre({"cart_key": cle, "cart": sorted(skus),
                   "items": enrichir(lignes, limit, ("category", "score", "requires", "provides"))})


@router.get("/customers/{customer_id}/recommendations",
            summary="Recommandations personnalisées à partir des achats du client (R6), avec leur raison")
def pour_le_client(customer_id: str, limit: int = Query(5, ge=1, le=20)):
    try:
        lignes = graphe(POUR_LE_CLIENT, client=customer_id, limite=limit * 2)
    except GrapheIndisponible as e:
        raise HTTPException(503, f"Graphe indisponible : {str(e)[:120]}")
    return propre({"customer_id": customer_id, "items": enrichir(lignes, limit)})
