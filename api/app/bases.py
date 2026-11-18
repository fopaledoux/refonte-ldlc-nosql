"""
Connexions aux quatre bases et règles partagées avec les lots.

Une seule connexion par base et par processus, ouverte au premier usage : chaque pilote
gère lui-même son pool de connexions. Les paramètres métier (durées de vie, limite par
défaut, buckets, shards) sont ceux des Lots 2 et 3 : l'API ne les réinvente pas.
"""
import json
import os
import time
import urllib.error
import urllib.request
import uuid
import zlib
from datetime import date, datetime, timezone
from decimal import Decimal

import redis
from bson import Decimal128, ObjectId
from cassandra import ConsistencyLevel
from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile
from cassandra.policies import DCAwareRoundRobinPolicy, TokenAwarePolicy
from cassandra.util import Date as DateCassandra
from pymongo import MongoClient

# --- Paramètres métier (Lot 2 : lot2-panier-sessions/lib/commun.sh) -------------------
TTL_SESSION = 1800            # 30 minutes d'inactivité
TTL_INVITE = 604800           # 7 jours pour le panier d'un visiteur
TTL_CLIENT = 2592000          # 30 jours pour le panier d'un client
LIMITE_DEFAUT = 10            # quantité maximale par produit, hors produits limités

# --- Répartition (Lot 3 : lot3-logs-analytics/lib/commun.py, mêmes formules) ----------
BUCKETS = 16
SHARDS = 16


def bucket(event_id):
    return event_id.int % BUCKETS


def shard(sku):
    return zlib.crc32(sku.encode()) % SHARDS


# --- Connexions ---------------------------------------------------------------------------
_mongo = _redis = _cassandra = None


def mongo():
    """Base du catalogue (Lot 1)."""
    global _mongo
    if _mongo is None:
        client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
        _mongo = client["ldlc_catalogue"]
    return _mongo


def cache():
    """Redis : sessions et paniers (Lot 2)."""
    global _redis
    if _redis is None:
        _redis = redis.Redis(host=os.environ.get("REDIS_HOST", "localhost"), port=6379, decode_responses=True)
    return _redis


def cassandra():
    """Cassandra : événements, compteurs, classements, commandes, prix (Lot 3).
    Deux profils : LOCAL_ONE par défaut, LOCAL_QUORUM pour les commandes et les prix."""
    global _cassandra
    if _cassandra is None:
        equilibrage = TokenAwarePolicy(DCAwareRoundRobinPolicy(local_dc="dc1"))
        profils = {
            EXEC_PROFILE_DEFAULT: ExecutionProfile(load_balancing_policy=equilibrage,
                                                   consistency_level=ConsistencyLevel.LOCAL_ONE, request_timeout=10),
            "quorum": ExecutionProfile(load_balancing_policy=equilibrage,
                                       consistency_level=ConsistencyLevel.LOCAL_QUORUM, request_timeout=10),
        }
        cluster = Cluster([os.environ.get("CASSANDRA_HOST", "localhost")], execution_profiles=profils)
        for essai in range(10):
            try:
                _cassandra = cluster.connect("ldlc_analytics")
                break
            except Exception:                              # Cassandra encore en démarrage
                if essai == 9:
                    raise
                time.sleep(3)
    return _cassandra


class GrapheIndisponible(Exception):
    """Neo4j ne répond pas (ou refuse la requête) : la route se replie sur le Lot 1."""


def graphe(requete, delai=3, **parametres):
    """Neo4j : recommandations et compatibilités (Lot 4), par l'API HTTP de requêtes.
    Délai court : la recommandation est un bonus de la page, jamais une raison de la ralentir.
    Renvoie les lignes en dictionnaires."""
    corps = json.dumps({"statement": requete, "parameters": parametres}).encode()
    req = urllib.request.Request(os.environ.get("NEO4J_URL", "http://localhost:7474") + "/db/neo4j/query/v2",
                                 data=corps, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=delai) as r:
            reponse = json.load(r)
    except urllib.error.HTTPError as e:
        raise GrapheIndisponible(e.read().decode(errors="replace")[:300]) from None
    except (urllib.error.URLError, OSError) as e:          # refus de connexion, délai dépassé
        raise GrapheIndisponible(str(e)) from None
    champs = reponse["data"]["fields"]
    return [dict(zip(champs, valeurs)) for valeurs in reponse["data"]["values"]]


_preparees = {}


def preparer(cql):
    """Requête CQL préparée une fois, puis réutilisée (le serveur garde le plan)."""
    if cql not in _preparees:
        _preparees[cql] = cassandra().prepare(cql)
    return _preparees[cql]


# --- Mise en forme des réponses -----------------------------------------------------------
def propre(v):
    """Convertit les types des pilotes (Decimal128, ObjectId, UUID, dates...) en JSON simple.
    Les montants restent des chaînes : un JSON ne doit pas les transformer en flottants."""
    if isinstance(v, dict):
        return {k: propre(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [propre(x) for x in v]
    if isinstance(v, (Decimal128, Decimal, ObjectId, uuid.UUID, DateCassandra)):
        return str(v)
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(v, date):
        return v.isoformat()
    return v


def aujourdhui():
    return datetime.now(timezone.utc).date()
