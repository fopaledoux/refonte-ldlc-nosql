"""
Lot 3 — Job de classement (Q2 : top produits ; Q9 : recherches).

CQL ne sait pas trier sur un agrégat (« les SKU par somme de vues décroissante ») :
le classement est calculé ici, puis écrit déjà trié dans `rankings`, où il se lit
en une seule partition. En production, ce job tourne toutes les heures sur le
datacenter analytique, pour ne pas ralentir l'ingestion.

Il ne balaie jamais une table : toutes les partitions à lire se calculent à l'avance.
  - vues        : product_views_by_day, 16 shards par jour (16 requêtes par jour) ;
  - recherches  : events_by_hour, 24 heures x 16 buckets par jour (384 requêtes).

Lancer à la main :
  docker compose exec cassandra python3 /lot3/jobs/classements.py
"""
import sys
import time as chrono
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/lot3/lib")
import commun as C  # noqa: E402
from cassandra.concurrent import execute_concurrent_with_args  # noqa: E402

TOP_GLOBAL, TOP_CATEGORIE, TOP_RECHERCHES = 100, 20, 50
JOURS = 7


def lire_catalogue():
    """Nom et catégorie de chaque SKU, recopiés dans le classement (export du Lot 1)."""
    noms = {}
    with open("/lot3/data/produits.tsv", encoding="utf-8") as f:
        next(f)
        for ligne in f:
            champs = ligne.rstrip("\n").split("\t")
            noms[champs[0]] = (champs[5], champs[1])
    return noms


def lire(session, cql, cles):
    """Une requête par partition, en parallèle. Renvoie (lignes, partitions lues)."""
    ps = session.prepare(cql)
    lignes = []
    for ok, res in execute_concurrent_with_args(session, ps, cles, concurrency=32, raise_on_first_error=True):
        lignes.extend(res)
    return lignes, len(cles)


def main():
    debut = chrono.time()
    _, session = C.connecter()
    catalogue = lire_catalogue()
    maintenant = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    j = maintenant.date()
    jours = [j - timedelta(days=d) for d in range(JOURS)]
    lundi = j - timedelta(days=j.weekday())
    annee, semaine, _ = j.isocalendar()
    periodes = {f"day:{d}": [d] for d in jours}
    periodes[f"week:{annee}-W{semaine:02d}"] = [d for d in jours if d >= lundi]   # semaine calendaire en cours
    periodes[f"7d:{j}"] = jours                                                    # 7 jours glissants

    partitions = lignes_lues = 0
    vues, recherches, sans_resultat = {}, {}, {}
    for d in jours:
        lignes, n = lire(session, "SELECT sku, views FROM product_views_by_day WHERE day = ? AND shard = ?",
                         [(d, s) for s in range(C.SHARDS)])
        vues[d] = Counter({r.sku: r.views for r in lignes})
        partitions, lignes_lues = partitions + n, lignes_lues + len(lignes)
        # Filtrer dans UNE partition (clé complète) est acceptable : ALLOW FILTERING ne
        # devient un problème que sans clé de partition, quand il faut lire tout le cluster.
        lignes, n = lire(session, "SELECT search_query, results FROM events_by_hour "
                                  "WHERE day = ? AND hour = ? AND bucket = ? AND type = 'search' ALLOW FILTERING",
                         [(d, h, b) for h in range(24) for b in range(C.BUCKETS)])
        recherches[d] = Counter(r.search_query for r in lignes)
        sans_resultat[d] = Counter(r.search_query for r in lignes if r.results == 0)
        partitions, lignes_lues = partitions + n, lignes_lues + len(lignes)

    cql = ("INSERT INTO rankings (period, ranking, rank, item, label, category, value, computed_at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    a_ecrire = []

    def classer(periode, nom, compteur, n, avec_catalogue):
        for rang, (item, valeur) in enumerate(compteur.most_common(n), start=1):
            label, cat = catalogue.get(item, (item, None)) if avec_catalogue else (item, None)
            a_ecrire.append((periode, nom, rang, item, label, cat, valeur, maintenant))

    for periode, js in periodes.items():
        # Un SKU absent du catalogue (produit de test, produit supprimé) n'est pas classé
        total = sum((Counter({sku: n for sku, n in vues[d].items() if sku in catalogue}) for d in js), Counter())
        classer(periode, "products:all", total, TOP_GLOBAL, True)
        par_cat = {}
        for sku, n in total.items():
            par_cat.setdefault(catalogue.get(sku, ("", "inconnue"))[1], Counter())[sku] = n
        for cat, compteur in par_cat.items():
            classer(periode, f"products:{cat}", compteur, TOP_CATEGORIE, True)
        classer(periode, "searches:all", sum((recherches[d] for d in js), Counter()), TOP_RECHERCHES, False)
        classer(periode, "searches:no-result", sum((sans_resultat[d] for d in js), Counter()), TOP_RECHERCHES, False)

    requetes = C.lots_par_partition(cql, a_ecrire, lambda r: r[:2], max_lignes=20, coherence="quorum")
    C.executer_requetes(session, requetes)
    nb_classements = len({r[:2] for r in a_ecrire})
    print(f"[lot3] Job de classement : {partitions} partitions lues ({lignes_lues} lignes), "
          f"{nb_classements} classements écrits ({len(a_ecrire)} lignes) sur {len(periodes)} périodes, "
          f"en {chrono.time() - debut:.1f} s")


if __name__ == "__main__":
    main()
