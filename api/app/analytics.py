"""
Routes « analytics » : vues, classements, commandes et prix (Cassandra, Lot 3).

Chaque route lit UNE partition, comme les requêtes types Q1 à Q8 du Lot 3. La fiche
produit (catalogue.py) écrit aussi ici chaque vue : c'est le chemin d'écriture W1.
"""
import base64
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from .bases import aujourdhui, bucket, cassandra, preparer, propre, shard

router = APIRouter(tags=["Analytics : vues, classements, commandes, prix (Cassandra, Lot 3)"])


# --- Écriture d'une vue (W1 du Lot 3) -------------------------------------------------------
def enregistrer_vue(sku, session):
    """Une vue de fiche : journal brut, parcours de la visite, deux compteurs.
    Prototype : un incrément par vue ; en production, l'ingestion agrège sur quelques secondes."""
    maintenant = datetime.now(timezone.utc).replace(tzinfo=None)
    jour, event_id = maintenant.date(), uuid.uuid4()
    session = session or {}
    visite = uuid.UUID(session["visitId"]) if session.get("visitId") else None
    client, appareil = session.get("customerId"), session.get("device", "desktop")
    page = f"/fiche/{sku}"
    c = cassandra()
    futures = [
        c.execute_async(preparer(
            "INSERT INTO events_by_hour (day, hour, bucket, ts, event_id, type, visit_id, customer_id, sku, page, device) "
            "VALUES (?, ?, ?, ?, ?, 'product_view', ?, ?, ?, ?, ?)"),
            (jour, maintenant.hour, bucket(event_id), maintenant, event_id, visite, client, sku, page, appareil)),
        c.execute_async(preparer("UPDATE product_daily_counts SET views = views + 1 WHERE sku = ? AND day = ?"),
                        (sku, jour)),
        c.execute_async(preparer("UPDATE product_views_by_day SET views = views + 1 WHERE day = ? AND shard = ? AND sku = ?"),
                        (jour, shard(sku), sku)),
    ]
    if visite:
        futures.append(c.execute_async(preparer(
            "INSERT INTO events_by_visit (visit_id, ts, event_id, device, type, customer_id, sku, page) "
            "VALUES (?, ?, ?, ?, 'product_view', ?, ?, ?)"),
            (visite, maintenant, event_id, appareil, client, sku, page)))
    for f in futures:
        f.result()                                            # quelques millisecondes en tout


def vues_du_jour(sku):
    ligne = cassandra().execute(preparer("SELECT views, cart_adds FROM product_daily_counts WHERE sku = ? AND day = ?"),
                                (sku, aujourdhui())).one()
    return {"views": ligne.views if ligne else 0, "cart_adds": ligne.cart_adds if ligne else 0}


# --- Lectures -------------------------------------------------------------------------------
@router.get("/products/{sku}/views", summary="Vues d'un produit aujourd'hui et sur 7 jours (Q1, Q7)")
def vues(sku: str):
    lignes = cassandra().execute(preparer(
        "SELECT day, views, cart_adds FROM product_daily_counts WHERE sku = ? AND day >= ? LIMIT 7"),
        (sku, aujourdhui() - timedelta(days=6)))
    jours = [{"day": l.day, "views": l.views, "cart_adds": l.cart_adds} for l in lignes]
    return propre({"sku": sku, "today": vues_du_jour(sku), "last_7_days": jours})


def _periode(periode):
    j = aujourdhui()
    if periode == "week":
        annee, semaine, _ = j.isocalendar()
        return f"week:{annee}-W{semaine:02d}"
    return f"{'7d' if periode == '7d' else 'day'}:{j}"


@router.get("/rankings/products", summary="Top des produits consultés, déjà calculé par le job du Lot 3 (Q2)")
def classement(period: str = Query("week", pattern="^(week|7d|day)$"),
               category: str = Query("all", description="all, ou l'identifiant d'une catégorie (processeurs...)"),
               limit: int = Query(10, ge=1, le=100)):
    lignes = list(cassandra().execute(preparer(
        "SELECT rank, item, label, category, value, computed_at FROM rankings WHERE period = ? AND ranking = ? LIMIT ?"),
        (_periode(period), f"products:{category}", limit)))
    if not lignes:
        raise HTTPException(404, f"Aucun classement pour {_periode(period)} / {category} : le job du Lot 3 a-t-il tourné ?")
    return propre({"period": _periode(period), "ranking": f"products:{category}",
                   "computed_at": lignes[0].computed_at,
                   "items": [{"rank": l.rank, "sku": l.item, "name": l.label, "category": l.category,
                              "views": l.value} for l in lignes]})


@router.get("/customers/{customer_id}/orders", summary="Historique des commandes d'un client, paginé (Q3)")
def commandes(customer_id: str, limit: int = Query(5, ge=1, le=50),
              page: str | None = Query(None, description="Jeton de la page suivante (champ next de la réponse)")):
    lie = preparer("SELECT ordered_at, order_id, status, total, currency, item_count, lines FROM orders_by_customer "
                   "WHERE customer_id = ?").bind((customer_id,))
    lie.fetch_size = limit
    etat = base64.urlsafe_b64decode(page) if page else None
    res = cassandra().execute(lie, execution_profile="quorum", paging_state=etat)
    suivant = base64.urlsafe_b64encode(res.paging_state).decode() if res.paging_state else None
    return propre({"customer_id": customer_id, "orders": [{
        "order_id": c.order_id, "ordered_at": c.ordered_at, "status": c.status, "total": c.total,
        "currency": c.currency, "item_count": c.item_count,
        "lines": [{"sku": l.sku, "name": l.name, "quantity": l.quantity, "unit_price": l.unit_price} for l in c.lines],
    } for c in res.current_rows], "next": suivant})


@router.get("/orders/{order_id}", summary="Une commande par son numéro, avec son historique de statuts (Q4)")
def commande(order_id: str):
    c = cassandra().execute(preparer("SELECT * FROM orders_by_id WHERE order_id = ?"), (order_id,),
                            execution_profile="quorum").one()
    if not c:
        raise HTTPException(404, f"Commande inconnue : {order_id}")
    return propre({"order_id": c.order_id, "customer_id": c.customer_id, "ordered_at": c.ordered_at,
                   "status": c.status, "total": c.total, "currency": c.currency,
                   "status_history": [{"at": k, "status": v} for k, v in sorted((c.status_history or {}).items())],
                   "lines": [{"sku": l.sku, "name": l.name, "quantity": l.quantity, "unit_price": l.unit_price}
                             for l in c.lines]})


@router.get("/products/{sku}/price-history",
            summary="Historique des prix et prix de référence d'une promotion : le plus bas des 30 jours (Q8)")
def historique_prix(sku: str):
    c = cassandra()
    historique = list(c.execute(preparer(
        "SELECT changed_at, price, regular_price, reason FROM price_history_by_product WHERE sku = ? LIMIT 20"),
        (sku,), execution_profile="quorum"))
    if not historique:
        raise HTTPException(404, f"Pas d'historique de prix pour {sku}")
    debut = datetime.combine(aujourdhui(), datetime.min.time()) - timedelta(days=30)
    avant = c.execute(preparer("SELECT price FROM price_history_by_product WHERE sku = ? AND changed_at <= ? LIMIT 1"),
                      (sku, debut), execution_profile="quorum").one()
    pendant = c.execute(preparer("SELECT min(price) AS lowest FROM price_history_by_product WHERE sku = ? AND changed_at > ?"),
                        (sku, debut), execution_profile="quorum").one()
    candidats = [x for x in ((avant.price if avant else None), (pendant.lowest if pendant else None)) if x is not None]
    return propre({"sku": sku, "current_price": historique[0].price,
                   "reference_price_30_days": min(candidats) if candidats else None,
                   "history": [{"changed_at": h.changed_at, "price": h.price, "regular_price": h.regular_price,
                                "reason": h.reason} for h in historique]})
