"""
Lot 3 — Export vers le Lot 4 (graphe de recommandations), au format TSV

  commandes  une ligne par article commandé (orders_by_id, balayage complet)
             -> lot4-recommandations/data/commandes.tsv
  vues       vues de fiches produit par les clients connectés, sur 7 jours
             (events_by_hour, partitions énumérées comme dans le job de classement)
             -> lot4-recommandations/data/vues.tsv

En production, le graphe est alimenté en continu par le même flux d'événements.
Ici, l'export est un instantané versionné dans lot4-recommandations/data/, pour que
le Lot 4 se lance sans Cassandra. Un balayage complet est acceptable pour un export
hors ligne, jamais pour une requête du site.

Depuis la racine du dépôt, une seule commande régénère les deux fichiers :
  docker compose run --rm cassandra-init python3 /lot3/jobs/export_graphe.py
Un seul export, sur la sortie standard :
  docker compose run --rm -T cassandra-init python3 /lot3/jobs/export_graphe.py commandes
"""
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/lot3/lib")
import commun as C  # noqa: E402
from cassandra.concurrent import execute_concurrent_with_args  # noqa: E402
from cassandra.query import SimpleStatement  # noqa: E402

FICHIERS = {
    "commandes": ("/export/lot4/commandes.tsv", "lot4-recommandations/data/commandes.tsv"),
    "vues": ("/export/lot4/vues.tsv", "lot4-recommandations/data/vues.tsv"),
}


def jour_de_reference(session):
    """Le dernier jour calculé par le job de classement : aujourd'hui (UTC), sauf si les
    données ont été chargées un autre jour (même règle que queries/01-lectures.py)."""
    aujourdhui = datetime.now(timezone.utc).date()
    ps = session.prepare("SELECT rank FROM rankings WHERE period = ? AND ranking = 'products:all' LIMIT 1")
    for recul in range(60):
        if session.execute(ps, (f"7d:{aujourdhui - timedelta(days=recul)}",)).one():
            return aujourdhui - timedelta(days=recul)
    return aujourdhui


def commandes(session):
    yield "order_id\tcustomer_id\tordered_at\tstatus\tsku\tquantity\tunit_price"
    requete = SimpleStatement("SELECT order_id, customer_id, ordered_at, status, lines FROM orders_by_id",
                              fetch_size=2000)
    lignes = []
    for c in session.execute(requete):
        for l in c.lines:
            lignes.append((c.order_id, c.customer_id, c.ordered_at.strftime("%Y-%m-%dT%H:%M:%S"), c.status,
                           l.sku, str(l.quantity), str(l.unit_price)))
    for l in sorted(lignes):
        yield "\t".join(l)


def vues(session, jours=7):
    yield "customer_id\tsku\tviews\tlast_viewed_at"
    jour = jour_de_reference(session)
    ps = session.prepare("SELECT customer_id, sku, ts FROM events_by_hour WHERE day = ? AND hour = ? AND bucket = ? "
                         "AND type = 'product_view' ALLOW FILTERING")
    cles = [(jour - timedelta(days=d), h, b) for d in range(jours) for h in range(24) for b in range(C.BUCKETS)]
    compte, derniere = defaultdict(int), {}
    for ok, res in execute_concurrent_with_args(session, ps, cles, concurrency=32, raise_on_first_error=True):
        for r in res:
            if r.customer_id and r.sku:
                cle = (r.customer_id, r.sku)
                compte[cle] += 1
                derniere[cle] = max(derniere.get(cle, r.ts), r.ts)
    for (client, sku) in sorted(compte):
        yield f"{client}\t{sku}\t{compte[(client, sku)]}\t{derniere[(client, sku)]:%Y-%m-%dT%H:%M:%S}"


EXPORTS = {"commandes": commandes, "vues": vues}


def main():
    demandes = sys.argv[1:] or list(EXPORTS)
    inconnus = [d for d in demandes if d not in EXPORTS]
    if inconnus:
        sys.exit(f"Export inconnu : {', '.join(inconnus)} (choix : {', '.join(EXPORTS)})")
    cluster, session = C.connecter()
    try:
        if sys.argv[1:]:                                   # un export précis : sur la sortie standard
            for nom in demandes:
                for ligne in EXPORTS[nom](session):
                    print(ligne)
            return
        for nom in demandes:                               # sans argument : les deux fichiers
            debut = time.time()
            contenu = "".join(ligne + "\n" for ligne in EXPORTS[nom](session))
            chemin, dans_le_depot = FICHIERS[nom]
            ancien = open(chemin, encoding="utf-8").read() if os.path.exists(chemin) else None
            if contenu != ancien:
                with open(chemin, "w", encoding="utf-8", newline="\n") as f:
                    f.write(contenu)
            etat = "inchangé" if contenu == ancien else "mis à jour"
            print(f"[export lot4] {dans_le_depot} : {contenu.count(chr(10))} lignes, {etat} "
                  f"({time.time() - debut:.0f} s)", file=sys.stderr)
    finally:
        cluster.shutdown()


if __name__ == "__main__":
    main()
