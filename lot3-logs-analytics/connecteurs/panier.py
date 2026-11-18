"""
Lot 3 — Connecteur du panier : flux Redis `events:cart` (Lot 2) vers Cassandra.

Le Lot 2 écrit chaque modification de panier dans le Stream `events:cart`, dans la
même opération atomique que la modification (outbox). Ce connecteur le lit avec un
groupe de consommateurs Redis, ce qui donne une livraison « au moins une fois » :

  1. XREADGROUP : lire un lot d'événements (d'abord ceux restés en attente après
     un arrêt brutal, puis les nouveaux) ;
  2. écrire dans Cassandra : le journal brut (events_by_hour), puis les compteurs
     agrégés sur le lot (un seul incrément par produit et par jour) ;
  3. XACK : acquitter le lot, seulement une fois Cassandra à jour.

Si le connecteur s'arrête entre 2 et 3, le lot est relu au redémarrage :
  - le journal brut est idempotent : event_id est dérivé de l'identifiant Redis,
    rejouer réécrit les mêmes lignes ;
  - les compteurs ne le sont pas : le lot rejoué est compté deux fois. Surcomptage
    borné à un lot (500 événements), accepté pour des statistiques de vues.

Lancer :  docker compose exec cassandra python3 /lot3/connecteurs/panier.py [--suivre]
"""
import os
import socket
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, "/lot3/lib")
import commun as C  # noqa: E402

FLUX, GROUPE, CONSOMMATEUR = "events:cart", "lot3-cassandra", "connecteur-1"
TAILLE_LOT = 500
ESPACE_UUID = uuid.uuid5(uuid.NAMESPACE_URL, "redis://ldlc/events:cart")
TYPES = {"add": "cart_add", "remove": "cart_remove", "set": "cart_set", "merge": "cart_merge"}


class Redis:
    """Client RESP minimal (le protocole de Redis), pour ne rien installer dans l'image."""

    def __init__(self, hote=None, port=6379):
        self.sock = socket.create_connection((hote or os.environ.get("REDIS_HOST", "redis"), port))
        self.fichier = self.sock.makefile("rb")

    def cmd(self, *args):
        morceaux = [str(a).encode() for a in args]
        self.sock.sendall(b"*%d\r\n" % len(morceaux)
                          + b"".join(b"$%d\r\n%s\r\n" % (len(m), m) for m in morceaux))
        return self._lire()

    def _lire(self):
        ligne = self.fichier.readline()
        genre, reste = ligne[:1], ligne[1:-2]
        if genre == b"+":
            return reste.decode()
        if genre == b"-":
            raise RuntimeError(reste.decode())
        if genre == b":":
            return int(reste)
        if genre == b"$":
            n = int(reste)
            return None if n < 0 else self.fichier.read(n + 2)[:-2].decode()
        if genre == b"*":
            n = int(reste)
            return None if n < 0 else [self._lire() for _ in range(n)]
        raise RuntimeError(f"réponse Redis inattendue : {ligne!r}")


INS_HEURE = ("INSERT INTO events_by_hour (day, hour, bucket, ts, event_id, type, customer_id, sku, "
             "target, quantity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
MAJ_PRODUIT = "UPDATE product_daily_counts SET cart_adds = cart_adds + ? WHERE sku = ? AND day = ?"


def convertir(id_redis, champs):
    """Entrée du Stream -> ligne de events_by_hour (et quantité ajoutée au panier)."""
    e = dict(zip(champs[::2], champs[1::2]))
    ts = datetime.fromtimestamp(int(e["ts"]) / 1000, tz=timezone.utc).replace(tzinfo=None)
    event_id = uuid.uuid5(ESPACE_UUID, id_redis)            # même entrée Redis -> même event_id
    panier = e.get("cart", "")
    client = panier.split(":")[2] if panier.startswith("cart:customer:") else None
    type_ = TYPES.get(e.get("type"), "cart_" + e.get("type", "unknown"))
    quantite = int(e["qty"]) if "qty" in e else (int(e["lines"]) if "lines" in e else None)
    ligne = (ts.date(), ts.hour, C.bucket(event_id), ts, event_id, type_, client, e.get("sku"),
             e.get("from"), quantite)
    ajout = quantite if type_ == "cart_add" and e.get("sku") else 0
    return ligne, ajout


def traiter_lot(session, redis, entrees):
    lignes, ajouts = [], Counter()
    for id_redis, champs in entrees:
        ligne, ajout = convertir(id_redis, champs)
        lignes.append(ligne)
        if ajout:
            ajouts[(ligne[7], ligne[0])] += ajout                 # agrégé sur le lot
    C.executer_requetes(session, C.lots_par_partition(INS_HEURE, lignes, lambda r: r[:3]))
    compteurs = [(n, sku, jour) for (sku, jour), n in ajouts.items()]
    C.executer_requetes(session, C.lots_par_partition(MAJ_PRODUIT, compteurs, lambda r: r[1], compteur=True))
    redis.cmd("XACK", FLUX, GROUPE, *[i for i, _ in entrees])  # après l'écriture, jamais avant
    return len(lignes), len(compteurs)


def executer(session=None, suivre=False, bavard=True):
    session = session or C.connecter()[1]
    redis = Redis()
    try:
        redis.cmd("XGROUP", "CREATE", FLUX, GROUPE, "0", "MKSTREAM")
    except RuntimeError as e:
        if "BUSYGROUP" not in str(e):
            raise
    total_evts = total_compteurs = 0
    position = "0"                                         # d'abord les événements en attente
    while True:
        args = ["XREADGROUP", "GROUP", GROUPE, CONSOMMATEUR, "COUNT", TAILLE_LOT]
        if suivre and position == ">":
            args += ["BLOCK", 5000]
        reponse = redis.cmd(*args, "STREAMS", FLUX, position)
        entrees = reponse[0][1] if reponse else []
        if not entrees:
            if position == "0":
                position = ">"                             # plus rien en attente : les nouveaux
                continue
            if suivre:
                continue
            break
        n, k = traiter_lot(session, redis, entrees)
        total_evts, total_compteurs = total_evts + n, total_compteurs + k
    if bavard:
        print(f"   [connecteur] {total_evts} événements du panier écrits, {total_compteurs} compteurs incrémentés, "
              f"en attente : {redis.cmd('XPENDING', FLUX, GROUPE)[0]}")
    return total_evts


if __name__ == "__main__":
    executer(suivre="--suivre" in sys.argv)
