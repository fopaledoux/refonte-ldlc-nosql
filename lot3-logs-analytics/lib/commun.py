"""
Lot 3 — Code partagé par le chargement, le job de classement, le connecteur du
panier et les requêtes types.

Utilise le pilote Python livré avec Cassandra (celui de cqlsh) : rien à installer.
Le pilote en pur Python plafonne vers 1 000 requêtes par seconde et par processus.
Les écritures sont donc regroupées par partition et réparties sur plusieurs
processus. En production, le service d'ingestion utilise un pilote compilé
(Java, Go) ; le débit de Cassandra elle-même est mesuré avec cassandra-stress.
"""
import datetime
import glob
import os
import sys
import time
import zlib
from collections import defaultdict

# --- Pilote embarqué dans l'image Cassandra ----------------------------------
_LIB = "/opt/cassandra/lib"
_zip = glob.glob(f"{_LIB}/cassandra-driver-internal-only-*.zip")[0]
_ver = os.path.basename(_zip)[len("cassandra-driver-internal-only-"):-len(".zip")]
sys.path[:0] = ([f"{_zip}/cassandra-driver-{_ver}"]
                + glob.glob(f"{_LIB}/geomet-*.zip") + glob.glob(f"{_LIB}/pyasyncore-*.zip"))

from cassandra import ConsistencyLevel                          # noqa: E402
from cassandra.cluster import (Cluster, ExecutionProfile,       # noqa: E402
                               EXEC_PROFILE_DEFAULT)
from cassandra.concurrent import execute_concurrent             # noqa: E402
from cassandra.policies import (DCAwareRoundRobinPolicy,        # noqa: E402
                                TokenAwarePolicy)
from cassandra.query import (BatchStatement, BatchType,         # noqa: E402
                             UNSET_VALUE)

# --- Paramètres du modèle (identiques pour l'ingestion, les jobs et l'API) ---
KEYSPACE = "ldlc_analytics"
DC = "dc1"
BUCKETS = 16          # events_by_hour : buckets par heure
SHARDS = 16           # product_views_by_day : shards par jour
TTL_EVENEMENTS = 90 * 86400


def bucket(event_id):
    """Bucket d'un événement : réparti uniformément, connu de tous les lecteurs (0 à 15)."""
    return event_id.int % BUCKETS


def shard(sku):
    """Shard d'un produit : CRC32 du SKU, calculable dans n'importe quel langage."""
    return zlib.crc32(sku.encode()) % SHARDS


# --- Connexion -----------------------------------------------------------------
# Deux profils de cohérence, choisis requête par requête :
#   défaut  : LOCAL_ONE, pour les événements, les compteurs et les classements ;
#   quorum  : LOCAL_QUORUM, pour les commandes et les prix (2 réplicas sur 3).
def connecter(hote=None):
    hote = hote or os.environ.get("CASSANDRA_HOST", "127.0.0.1")
    equilibrage = TokenAwarePolicy(DCAwareRoundRobinPolicy(local_dc=DC))
    profils = {
        EXEC_PROFILE_DEFAULT: ExecutionProfile(
            load_balancing_policy=equilibrage,
            consistency_level=ConsistencyLevel.LOCAL_ONE, request_timeout=60),
        "quorum": ExecutionProfile(
            load_balancing_policy=equilibrage,
            consistency_level=ConsistencyLevel.LOCAL_QUORUM, request_timeout=60),
    }
    cluster = Cluster([hote], execution_profiles=profils)
    return cluster, cluster.connect(KEYSPACE)


# --- Écriture en masse : lots d'une seule partition, plusieurs processus ------
# Un BATCH non journalisé n'est efficace que si toutes ses lignes vont dans la
# même partition : Cassandra l'applique alors comme une seule écriture.
# Un lot multi-partitions charge le coordinateur sans rien garantir de plus.
def lots_par_partition(cql, lignes, cle, max_lignes=20, compteur=False, coherence="one"):
    """Regroupe les lignes par partition (cle(ligne)) en requêtes d'au plus max_lignes.
    max_lignes garde chaque lot sous le seuil d'alerte de Cassandra (5 Kio par défaut)."""
    groupes = defaultdict(list)
    for ligne in lignes:
        groupes[cle(ligne)].append(ligne)
    requetes = []
    for g in groupes.values():
        for i in range(0, len(g), max_lignes):
            requetes.append((cql, "counter" if compteur else "unlogged", g[i:i + max_lignes], coherence))
    return requetes


COHERENCES = {"one": ConsistencyLevel.LOCAL_ONE, "quorum": ConsistencyLevel.LOCAL_QUORUM}
_session = None
_preparees = {}


def sans_null(ligne):
    """Un champ absent est envoyé comme UNSET, jamais comme null : en Cassandra,
    écrire null crée une tombstone (une marque de suppression) pour chaque cellule."""
    return tuple(UNSET_VALUE if v is None else v for v in ligne)


def _executer_paquet(paquet):
    """Exécuté dans un processus de travail : une connexion par processus."""
    global _session
    if _session is None:
        _session = connecter()[1]
    return executer_requetes(_session, paquet)


def executer_requetes(session, paquet, concurrence=48):
    """Exécute des requêtes préparées par lots_par_partition sur une session donnée."""
    instructions = []
    for cql, mode, lignes, coherence in paquet:
        if cql not in _preparees:
            _preparees[cql] = session.prepare(cql)
        ps, cl = _preparees[cql], COHERENCES[coherence]
        if len(lignes) == 1:
            lie = ps.bind(sans_null(lignes[0]))
            lie.consistency_level = cl
            instructions.append((lie, ()))
        else:
            lot = BatchStatement(batch_type=BatchType.COUNTER if mode == "counter" else BatchType.UNLOGGED,
                                 consistency_level=cl)
            for ligne in lignes:
                lot.add(ps, sans_null(ligne))
            instructions.append((lot, ()))
    execute_concurrent(session, instructions, concurrency=concurrence, raise_on_first_error=True)
    return sum(len(r[2]) for r in paquet)


class Chargeur:
    """Pool de processus d'écriture. À créer avant toute connexion dans le processus principal."""

    def __init__(self, processus=3):
        import multiprocessing
        self.pool = multiprocessing.get_context("fork").Pool(processus)

    def ecrire(self, requetes, taille_paquet=150):
        debut = time.time()
        paquets = [requetes[i:i + taille_paquet] for i in range(0, len(requetes), taille_paquet)]
        lignes = sum(self.pool.imap_unordered(_executer_paquet, paquets))
        duree = time.time() - debut
        return lignes, len(requetes), duree

    def fermer(self):
        self.pool.close()
        self.pool.join()


# --- Affichage des requêtes types ---------------------------------------------
def titre(texte, sous_titre=None):
    print()
    print("=" * 78)
    print(texte)
    if sous_titre:
        print("   " + sous_titre)
    print("=" * 78)


def note(texte):
    print("   -> " + texte)


def _texte(v):
    if v is None:
        return "null"
    if isinstance(v, float):
        return f"{v:.3f}"
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(v, "_fields"):                     # type utilisateur (UDT)
        return "{" + ", ".join(f"{f}: {_texte(getattr(v, f))}" for f in v._fields) + "}"
    if hasattr(v, "items"):                       # map
        return "{" + ", ".join(f"{_texte(k)[5:19]}: {_texte(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_texte(x) for x in v) + "]"
    return str(v)


def tableau(lignes, colonnes=None, largeur_max=42):
    """Affiche des lignes (namedtuples du pilote) comme cqlsh."""
    lignes = list(lignes)
    if not lignes:
        print("   (0 ligne)")
        return lignes
    colonnes = colonnes or list(lignes[0]._fields)
    cellules = [[_texte(getattr(l, c))[:largeur_max] for c in colonnes] for l in lignes]
    largeurs = [max(len(c), *(len(r[i]) for r in cellules)) for i, c in enumerate(colonnes)]
    print("   " + " | ".join(c.ljust(w) for c, w in zip(colonnes, largeurs)))
    print("   " + "-+-".join("-" * w for w in largeurs))
    for r in cellules:
        print("   " + " | ".join(v.ljust(w) for v, w in zip(r, largeurs)))
    print(f"   ({len(lignes)} ligne{'s' if len(lignes) > 1 else ''})")
    return lignes


def _litteral(v):
    if hasattr(v, "_fields"):                     # type utilisateur (UDT) : {champ: valeur}
        return "{" + ", ".join(f"{f}: {_litteral(getattr(v, f))}" for f in v._fields) + "}"
    if isinstance(v, list) and v and hasattr(v[0], "_fields"):
        return "[" + ", ".join(_litteral(x) for x in v) + "]"     # list<frozen<udt>>
    if isinstance(v, (list, tuple)):
        return "(" + ", ".join(_litteral(x) for x in v) + ")"     # IN (...)
    if isinstance(v, datetime.datetime):
        return "'" + v.strftime("%Y-%m-%d %H:%M:%S") + "'"
    if isinstance(v, (str, datetime.date)):
        return "'" + str(v) + "'"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_litteral(k)}: {_litteral(x)}" for k, x in v.items()) + "}"
    return "null" if v is None else str(v)


def montrer(cql, params=()):
    """Affiche la requête CQL avec ses valeurs, comme on la taperait dans cqlsh."""
    texte = cql
    for p in params:
        texte = texte.replace("?", _litteral(p), 1)
    print("cqlsh> " + " ".join(texte.split()))


def executer(session, cql, params=(), profil=EXEC_PROFILE_DEFAULT, tracer=False, colonnes=None,
             afficher=True, largeur_max=42):
    """Affiche la requête, l'exécute (préparée), affiche le résultat et, sur demande, la trace."""
    montrer(cql, params)
    ps = session.prepare(cql)
    debut = time.perf_counter()
    res = session.execute(ps, params, execution_profile=profil, trace=tracer)
    duree_ms = (time.perf_counter() - debut) * 1000
    lignes = list(res)
    if afficher:
        tableau(lignes, colonnes, largeur_max)
    if tracer:
        resume_trace(res.get_query_trace(max_wait_sec=5), duree_ms)
    return lignes


def ecrire(session, cql, params=(), profil=EXEC_PROFILE_DEFAULT):
    """Affiche et exécute une écriture (préparée) ; les valeurs None partent en UNSET."""
    montrer(cql, params)
    debut = time.perf_counter()
    session.execute(session.prepare(cql), sans_null(params), execution_profile=profil)
    print(f"   OK ({(time.perf_counter() - debut) * 1000:.1f} ms)")


def lot(session, instructions, type_lot=BatchType.LOGGED, coherence=ConsistencyLevel.LOCAL_QUORUM):
    """Affiche et exécute un BATCH : instructions = [(cql, params), ...]."""
    nom = {BatchType.LOGGED: "", BatchType.UNLOGGED: "UNLOGGED ", BatchType.COUNTER: "COUNTER "}[type_lot]
    print(f"cqlsh> BEGIN {nom}BATCH   -- cohérence {ConsistencyLevel.value_to_name[coherence]}")
    b = BatchStatement(batch_type=type_lot, consistency_level=coherence)
    for cql, params in instructions:
        texte = cql
        for p in params:
            texte = texte.replace("?", _litteral(p), 1)
        print("         " + " ".join(texte.split()) + ";")
        b.add(session.prepare(cql), sans_null(params))
    print("       APPLY BATCH;")
    debut = time.perf_counter()
    session.execute(b)
    print(f"   OK ({(time.perf_counter() - debut) * 1000:.1f} ms)")


def resume_trace(trace, duree_client_ms=None):
    """Équivalent du explain() du Lot 1 : partitions, SSTables et lignes lues côté serveur."""
    lus = [e.description for e in trace.events
           if e.description.startswith(("Executing single-partition", "Merged data from",
                                        "Read ", "Skipped", "Partition index",
                                        "Bloom filter", "Executing read on"))]
    print(f"   [trace] serveur {trace.duration.total_seconds() * 1000:.2f} ms"
          + (f", client {duree_client_ms:.2f} ms" if duree_client_ms else ""))
    vus = set()
    for d in lus:
        if d not in vus:
            print("   [trace] " + d)
            vus.add(d)
