"""
Lot 1 — Outils partagés : connexion, affichage des résultats et des plans d'exécution,
et quelques règles de conversion qui garantissent des données identiques d'un
chargement à l'autre (types BSON, arrondis, écriture des nombres).
"""
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from bson import Decimal128, ObjectId
from pymongo import MongoClient

BASE = "ldlc_catalogue"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
INT32 = 2 ** 31


def connecter():
    """La base du catalogue. MONGO_URI vient du conteneur (mongodb://mongodb:27017) ;
    MONGO_DB permet de charger une autre base (copie temporaire des exports)."""
    client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
    return client[os.environ.get("MONGO_DB", BASE)]


# --- Types et arrondis ---------------------------------------------------------
def entier_ou_reel(v):
    """Type BSON d'un nombre : entier 32 bits s'il est entier et tient sur 32 bits,
    réel sinon. Un « 4.0 » du jeu source devient l'entier 4, un très grand entier un réel."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v if -INT32 <= v < INT32 else float(v)
    if isinstance(v, float) and v.is_integer() and -INT32 <= v < INT32 and not (v == 0 and math.copysign(1, v) < 0):
        return int(v)
    return v


def normaliser(doc):
    """Applique entier_ou_reel à tout un document (dictionnaires et listes imbriqués)."""
    if isinstance(doc, dict):
        return {k: normaliser(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [normaliser(v) for v in doc]
    return entier_ou_reel(doc)


def arrondi(x):
    """Arrondi à l'entier, les demis vers le haut : round(2.5) vaut 3 (Python arrondirait à 2)."""
    return math.floor(x + 0.5)


def decimales(x, n):
    """Écriture à n décimales, les demis vers le haut (159.945 -> 159.95)."""
    return str(Decimal(x).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP))


def texte(v):
    """Écriture d'une valeur dans un message : 4.0 s'écrit 4, True s'écrit true."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def json_compact(v):
    """JSON compact, avec les nombres écrits comme texte()."""
    return json.dumps(normaliser(v), ensure_ascii=False, separators=(",", ":"))


def date_ms(ms):
    """Date UTC à partir d'un nombre de millisecondes depuis 1970."""
    return EPOCH + timedelta(milliseconds=ms)


def maintenant():
    """L'instant présent, à la milliseconde (la précision des dates BSON)."""
    t = datetime.now(timezone.utc)
    return t.replace(microsecond=t.microsecond // 1000 * 1000)


# --- Affichage ---------------------------------------------------------------------
def titre(texte_titre, explication=None):
    print("\n" + "=" * 78)
    print(texte_titre)
    if explication:
        print("   " + explication)
    print("=" * 78)


def _etapes(noeud):
    """Chaîne des étapes du plan gagnant, ex. « FETCH <- IXSCAN(ux_slug) »."""
    sortie = []
    n = noeud
    while n:
        sortie.append(n["stage"] + (f"({n['indexName']})" if n.get("indexName") else ""))
        branches = n.get("inputStages") or []
        if len(branches) > 1:
            sortie.append(f"[{len(branches)} branches : {' | '.join(_etapes(b) for b in branches)}]")
            break
        n = n.get("inputStage") or (branches[0] if branches else None)
    return " <- ".join(sortie)


def plan(explain):
    """Résumé lisible d'un explain() de find() : étapes, clés et documents lus."""
    qp = explain["queryPlanner"]
    gagnant = qp["winningPlan"].get("queryPlan") or qp["winningPlan"]
    es = explain["executionStats"]
    print(f"   Plan    : {_etapes(gagnant)}")
    print(f"   Lu      : {es['totalKeysExamined']} clés d'index, {es['totalDocsExamined']} documents"
          f" -> {es['nReturned']} renvoyés en {es['executionTimeMillis']} ms")


def _cellule(v):
    if v is None:
        return "-"
    if isinstance(v, (datetime, date)):
        return v.isoformat()[:10]
    return texte(v)


def tableau(lignes, colonnes):
    """Affiche des lignes (dictionnaires) sous forme de tableau aligné."""
    lignes = list(lignes)
    largeurs = [max([len(c)] + [len(_cellule(l.get(c))) for l in lignes]) for c in colonnes]
    print("   " + " | ".join(c.ljust(w) for c, w in zip(colonnes, largeurs)))
    print("   " + "-+-".join("-" * w for w in largeurs))
    for l in lignes:
        print("   " + " | ".join(_cellule(l.get(c)).ljust(w) for c, w in zip(colonnes, largeurs)))


def _lisible(v):
    if isinstance(v, (Decimal128, ObjectId)):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    raise TypeError(type(v).__name__)


def afficher(obj):
    """Affiche un document de façon lisible (équivalent de printjson)."""
    print(json.dumps(normaliser(obj), ensure_ascii=False, indent=2, default=_lisible))
