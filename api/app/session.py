"""
Sessions et panier (Redis, Lot 2), par les fonctions Lua atomiques de la bibliothèque « ldlc ».

L'API ne lit ni n'écrit jamais une clé de panier directement : elle appelle cart_add,
cart_set, cart_get, cart_merge, session_create, session_touch et session_login (FCALL).
La session est transmise dans l'en-tête X-Session-Id.
"""
import secrets
import uuid

import redis
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .bases import LIMITE_DEFAUT, TTL_CLIENT, TTL_INVITE, TTL_SESSION, cache, mongo, propre

router = APIRouter(tags=["Session et panier (Redis, Lot 2)"])
CODES_HTTP = {"NOTSELLABLE": 409, "NOLINE": 404, "BADQTY": 422, "BADTTL": 422, "NOSESSION": 401, "EXISTS": 409}


def erreur_redis(e):
    """Les fonctions Lua renvoient « CODE message » : le code devient le statut HTTP."""
    code = str(e).split()[0]
    return HTTPException(CODES_HTTP.get(code, 500), str(e))


def en_dict(paires):
    return dict(zip(paires[::2], paires[1::2])) if paires else {}


def lire(session_id):
    """Session courante, prolongée de 30 min (TTL glissant), ou None si elle a expiré."""
    return en_dict(cache().fcall("session_touch", 1, f"session:{session_id}", TTL_SESSION)) or None


def exiger(session_id):
    s = lire(session_id) if session_id else None
    if not s:
        raise HTTPException(401, "Session absente ou expirée : créer une session avec POST /sessions")
    return s


def ttl_panier(cle):
    return TTL_CLIENT if cle.startswith("cart:customer:") else TTL_INVITE


# --- Modèles des requêtes ------------------------------------------------------------------
class NouvelleSession(BaseModel):
    device: str = Field("desktop", pattern="^(desktop|mobile|app)$")


class Connexion(BaseModel):
    customer_id: str = Field(..., pattern=r"^CUST-\d{6}$", examples=["CUST-000777"])


class Ligne(BaseModel):
    sku: str = Field(..., examples=["GPU-000009"])
    quantity: int = Field(1, ge=1, le=99)


class Quantite(BaseModel):
    quantity: int = Field(..., ge=0, le=99, description="0 retire la ligne")


# --- Sessions -------------------------------------------------------------------------------
@router.post("/sessions", status_code=201, summary="Ouvrir une session de visiteur, avec son panier invité")
def ouvrir(corps: NouvelleSession = NouvelleSession()):
    session_id, panier = secrets.token_hex(16), f"cart:guest:{secrets.token_hex(12)}"
    contenu = en_dict(cache().fcall("session_create", 1, f"session:{session_id}", TTL_SESSION,
                                    "locale", "fr-FR", "device", corps.device, "visitId", str(uuid.uuid4()),
                                    "cartKey", panier))
    return {"session_id": session_id, "expires_in_s": TTL_SESSION, "session": contenu}


@router.get("/sessions/me", summary="Lire la session (et la prolonger)")
def moi(x_session_id: str | None = Header(None)):
    return {"session_id": x_session_id, "session": exiger(x_session_id)}


@router.post("/sessions/login", summary="Connexion : nouvel identifiant de session, panier invité fusionné")
def connexion(corps: Connexion, x_session_id: str | None = Header(None)):
    s = exiger(x_session_id)
    client, nouvel_id = corps.customer_id, secrets.token_hex(16)
    panier_client = f"cart:customer:{client}"
    try:
        contenu = en_dict(cache().fcall("session_login", 3, f"session:{x_session_id}", f"session:{nouvel_id}",
                                        f"customer:{client}:sessions", client, nouvel_id, panier_client,
                                        TTL_SESSION, TTL_CLIENT))
        lignes = cache().fcall("cart_merge", 4, s.get("cartKey", ""), panier_client, "catalog:limits", "events:cart",
                               TTL_CLIENT, LIMITE_DEFAUT) if s.get("cartKey", "").startswith("cart:guest:") else 0
    except redis.ResponseError as e:
        raise erreur_redis(e)
    return {"session_id": nouvel_id, "previous_session_id_valid": False, "customer_id": client,
            "merged_cart_lines": lignes, "session": contenu}


@router.post("/sessions/logout", summary="Déconnexion : la session est supprimée, le panier client est conservé")
def deconnexion(x_session_id: str | None = Header(None)):
    s = exiger(x_session_id)
    with cache().pipeline(transaction=True) as tx:            # MULTI / EXEC
        tx.delete(f"session:{x_session_id}")
        if s.get("customerId"):
            tx.zrem(f"customer:{s['customerId']}:sessions", x_session_id)
        tx.execute()
    return {"logged_out": True}


# --- Panier ---------------------------------------------------------------------------------
@router.get("/cart", summary="Contenu du panier, avec le nom et le prix courant lus dans le catalogue")
def panier(x_session_id: str | None = Header(None)):
    s = exiger(x_session_id)
    cle = s["cartKey"]
    quantites = {k: int(v) for k, v in en_dict(cache().fcall("cart_get", 1, cle, ttl_panier(cle))).items()}
    # Le panier ne garde que SKU et quantité : nom et prix viennent du catalogue, en une requête $in
    fiches = {p["_id"]: p for p in mongo().products.find({"_id": {"$in": list(quantites)}},
                                                         {"name": 1, "slug": 1, "price": 1})}
    lignes, total = [], 0
    for sku, qte in sorted(quantites.items()):
        p = fiches.get(sku, {})
        prix = (p.get("price") or {}).get("amount")
        montant = prix.to_decimal() * qte if prix else None
        total += montant or 0
        lignes.append({"sku": sku, "name": p.get("name"), "slug": p.get("slug"), "quantity": qte,
                       "unit_price": prix, "line_total": montant})
    return propre({"cart_key": cle, "lines": lignes, "item_count": sum(quantites.values()), "total": total,
                   "currency": "USD"})


@router.post("/cart/items", summary="Ajouter un produit : vérifie qu'il est en vente et applique la limite d'achat")
def ajouter(corps: Ligne, x_session_id: str | None = Header(None)):
    cle = exiger(x_session_id)["cartKey"]
    try:
        dans_panier, limite, ajoute = cache().fcall("cart_add", 4, cle, "catalog:sellable", "catalog:limits",
                                                    "events:cart", corps.sku, corps.quantity, ttl_panier(cle),
                                                    LIMITE_DEFAUT)
    except redis.ResponseError as e:
        raise erreur_redis(e)
    return {"sku": corps.sku, "requested": corps.quantity, "added": ajoute, "in_cart": dans_panier,
            "limit": limite, "limited": ajoute < corps.quantity}


@router.put("/cart/items/{sku}", summary="Fixer la quantité d'une ligne (0 la retire)")
def modifier(sku: str, corps: Quantite, x_session_id: str | None = Header(None)):
    cle = exiger(x_session_id)["cartKey"]
    try:
        dans_panier, limite = cache().fcall("cart_set", 3, cle, "catalog:limits", "events:cart",
                                            sku, corps.quantity, ttl_panier(cle), LIMITE_DEFAUT)
    except redis.ResponseError as e:
        raise erreur_redis(e)
    return {"sku": sku, "in_cart": dans_panier, "limit": limite}


@router.delete("/cart", summary="Vider le panier (événement « clear » publié dans la même transaction)")
def vider(x_session_id: str | None = Header(None)):
    cle = exiger(x_session_id)["cartKey"]
    secondes, micro = cache().time()                          # horloge de Redis, comme les fonctions Lua
    with cache().pipeline(transaction=True) as tx:            # MULTI / EXEC : suppression + outbox
        tx.delete(cle)
        tx.xadd("events:cart", {"ts": secondes * 1000 + micro // 1000, "type": "clear", "cart": cle},
                maxlen=100000, approximate=True)
        supprime, _ = tx.execute()
    return propre({"cart_key": cle, "cleared": bool(supprime)})
