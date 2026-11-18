"""
Lot 3 — Requêtes types en lecture (Q1 à Q9), puis les requêtes que Cassandra refuse.

Chaque lecture affiche sa trace serveur (l'équivalent du explain() du Lot 1) :
partitions lues, SSTables fusionnés, lignes vivantes et tombstones.

  docker compose exec cassandra python3 /lot3/queries/01-lectures.py
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/lot3/lib")
import commun as C  # noqa: E402
from cassandra import InvalidRequest  # noqa: E402

_, s = C.connecter()
SORTIE = "GPU-000009"

# Jour de référence : le dernier passage du job de classement, c'est-à-dire aujourd'hui (UTC)
# sauf si les données ont été chargées un autre jour
AUJOURDHUI = datetime.now(timezone.utc).date()
J = AUJOURDHUI
ps_7j = s.prepare("SELECT rank FROM rankings WHERE period = ? AND ranking = 'products:all' LIMIT 1")
for recul in range(60):
    if s.execute(ps_7j, (f"7d:{AUJOURDHUI - timedelta(days=recul)}",)).one():
        J = AUJOURDHUI - timedelta(days=recul)
        break
if J != AUJOURDHUI:
    print(f"(données chargées le {J} : ce jour sert de référence pour « aujourd'hui »)")
# Jour de la sortie de la carte graphique : son pic de vues sur 30 jours
courbe = s.execute("SELECT day, views FROM product_daily_counts WHERE sku = %s AND day >= %s",
                   (SORTIE, J - timedelta(days=29)))
JOUR_SORTIE = max(courbe, key=lambda r: r.views).day.date()
annee, semaine, _ = J.isocalendar()
SEMAINE = f"week:{annee}-W{semaine:02d}"

# -----------------------------------------------------------------------------
C.titre("Q1. Combien de vues pour ce produit aujourd'hui ?",
        "product_daily_counts : partition = SKU, une ligne par jour (compteurs)")
C.executer(s, "SELECT day, views, cart_adds FROM product_daily_counts WHERE sku = ? AND day = ?",
           (SORTIE, J), tracer=True)
C.note("une partition, une ligne : le chiffre est déjà calculé, rien n'est compté à la lecture")
lancement = C.executer(s, "SELECT day, views, cart_adds FROM product_daily_counts WHERE sku = ? AND day = ?",
                       (SORTIE, JOUR_SORTIE))
if lancement:
    l = lancement[0]
    C.note(f"jour de la sortie : {l.views} vues, {l.cart_adds} ajouts au panier "
           f"(taux d'ajout {100 * l.cart_adds / max(l.views, 1):.1f} %)")

# -----------------------------------------------------------------------------
C.titre("Q7. Courbe des vues d'un produit sur 30 jours (back-office)",
        "même partition : une tranche de lignes, déjà triées par jour décroissant")
C.executer(s, "SELECT day, views, cart_adds FROM product_daily_counts WHERE sku = ? AND day >= ? LIMIT 10",
           (SORTIE, J - timedelta(days=29)), tracer=True)
C.note("LIMIT 10 pour l'affichage ; la clause day >= ? borne la tranche à 30 jours")

# -----------------------------------------------------------------------------
C.titre("Q2. Top des produits consultés cette semaine",
        f"rankings : partition = (période, classement), lignes triées par rang. Période {SEMAINE}")
top = C.executer(s, "SELECT rank, item, label, category, value, computed_at FROM rankings "
                    "WHERE period = ? AND ranking = ? LIMIT 10",
                 (SEMAINE, "products:all"), tracer=True,
                 colonnes=["rank", "item", "label", "category", "value"])
if top:
    C.note(f"calculé par le job à {top[0].computed_at:%Y-%m-%d %H:%M} UTC ; la lecture ne trie rien, "
           "les lignes sont rangées par rang sur le disque")
C.executer(s, "SELECT rank, item, label, value FROM rankings WHERE period = ? AND ranking = ? LIMIT 5",
           (SEMAINE, "products:processeurs"))
C.note("par catégorie : même table, autre partition (ranking = 'products:<catégorie>')")
C.executer(s, "SELECT rank, item, label, value FROM rankings WHERE period = ? AND ranking = ? LIMIT 5",
           (f"7d:{J}", "products:all"))
C.note("7 jours glissants : autre période, même requête")

# -----------------------------------------------------------------------------
C.titre("Q3. Historique des commandes d'un client, la plus récente d'abord",
        "orders_by_customer : partition = client, clustering (ordered_at DESC, order_id)")
cql = ("SELECT ordered_at, order_id, status, total, currency, item_count FROM orders_by_customer "
       "WHERE customer_id = ?")
C.montrer(cql + " -- page de 5", ("CUST-000042",))
ps = s.prepare(cql)
lie = ps.bind(("CUST-000042",))
lie.fetch_size = 5
page1 = s.execute(lie, execution_profile="quorum", trace=True)
C.tableau(page1.current_rows)
C.resume_trace(page1.get_query_trace(max_wait_sec=5))
etat = page1.paging_state
C.note(f"cohérence LOCAL_QUORUM ; page suivante : paging_state ({len(etat)} octets) renvoyé par le serveur")
page2 = s.execute(lie, execution_profile="quorum", paging_state=etat)
C.tableau(page2.current_rows)
C.note("la page 2 reprend exactement après la dernière ligne de la page 1, sans OFFSET")
derniere = page1.current_rows[0]
C.executer(s, "SELECT order_id, lines FROM orders_by_customer WHERE customer_id = ? AND ordered_at = ? AND order_id = ?",
           ("CUST-000042", derniere.ordered_at, derniere.order_id), profil="quorum", largeur_max=120)
C.note("les lignes de commande (type order_line) sont recopiées : nom et prix payé, sans jointure")

# -----------------------------------------------------------------------------
C.titre("Q4. Une commande par son numéro (service client)",
        "orders_by_id : partition = numéro de commande")
C.executer(s, "SELECT order_id, customer_id, ordered_at, status, status_history, total FROM orders_by_id "
              "WHERE order_id = ?", (derniere.order_id,), profil="quorum", tracer=True, largeur_max=150)
C.note("status_history (map<timestamp, text>) garde chaque étape ; customer_id et ordered_at "
       "permettent de mettre à jour orders_by_customer")

# -----------------------------------------------------------------------------
C.titre("Q5. Parcours d'une visite, dans l'ordre",
        "events_by_visit : partition = visite, clustering (ts, event_id)")
candidates = s.execute(
    "SELECT visit_id FROM events_by_hour WHERE day = %s AND hour = 9 AND bucket = 0 "
    "AND type = 'search' ALLOW FILTERING", (JOUR_SORTIE,))
visite, meilleure = None, 0
for c in list(candidates)[:30]:
    n = s.execute("SELECT count(*) AS n FROM events_by_visit WHERE visit_id = %s", (c.visit_id,)).one().n
    if meilleure < n <= 14:
        visite, meilleure = c.visit_id, n
C.executer(s, "SELECT ts, device, type, sku, search_query, results, target FROM events_by_visit WHERE visit_id = ?",
           (visite,), tracer=True)
C.note(f"device est STATIC : stocké une fois par partition (la trace compte {meilleure} événements "
       "+ 1 ligne statique)")

# -----------------------------------------------------------------------------
C.titre("Q6. Événements bruts d'une heure (jobs, audit, export)",
        "events_by_hour : partition = (jour, heure, bucket) ; une heure = 16 partitions connues")
C.executer(s, "SELECT ts, type, sku, search_query, device FROM events_by_hour "
              "WHERE day = ? AND hour = ? AND bucket = ? LIMIT 5", (JOUR_SORTIE, 9, 0), tracer=True)
lignes = C.executer(s, "SELECT bucket, count(*) AS events FROM events_by_hour WHERE day = ? AND hour = ? "
                         "AND bucket IN ? GROUP BY day, hour, bucket",
                     (JOUR_SORTIE, 9, list(range(C.BUCKETS))), afficher=False)
valeurs = [l.events for l in lignes]
print("   " + "  ".join(f"b{l.bucket}:{l.events}" for l in lignes))
C.note(f"heure de la sortie : {sum(valeurs)} événements répartis en 16 partitions, de {min(valeurs)} à "
       f"{max(valeurs)} lignes : aucune partition chaude, même quand un seul produit concentre le trafic")

# -----------------------------------------------------------------------------
C.titre("Q8. Prix de référence d'une promotion : le plus bas des 30 derniers jours",
        "price_history_by_product : partition = SKU, clustering changed_at DESC")
PRODUIT = "MON-000351"
C.executer(s, "SELECT changed_at, price, regular_price, reason FROM price_history_by_product WHERE sku = ?",
           (PRODUIT,), profil="quorum")
debut = datetime.combine(J, datetime.min.time()) - timedelta(days=30)
avant = C.executer(s, "SELECT changed_at, price FROM price_history_by_product WHERE sku = ? AND changed_at <= ? LIMIT 1",
                   (PRODUIT, debut), profil="quorum", tracer=True)
pendant = C.executer(s, "SELECT min(price) AS lowest, count(*) AS changes FROM price_history_by_product "
                        "WHERE sku = ? AND changed_at > ?", (PRODUIT, debut), profil="quorum", tracer=True)
reference = min([r.price for r in avant] + ([pendant[0].lowest] if pendant and pendant[0].lowest else []))
courant = s.execute("SELECT price FROM price_history_by_product WHERE sku = %s LIMIT 1", (PRODUIT,)).one().price
C.note(f"prix en vigueur il y a 30 jours (1re requête) et plus bas prix depuis (2e) : référence {reference} $")
C.note(f"prix courant {courant} $ : une remise annoncée aujourd'hui se calcule sur {reference} $, pas sur {courant} $")

# -----------------------------------------------------------------------------
C.titre("Q9. Recherches les plus fréquentes et recherches sans résultat (7 jours)",
        "rankings : mêmes partitions précalculées, classements 'searches:all' et 'searches:no-result'")
C.executer(s, "SELECT rank, item, value FROM rankings WHERE period = ? AND ranking = ? LIMIT 8",
           (f"7d:{J}", "searches:all"))
C.executer(s, "SELECT rank, item, value FROM rankings WHERE period = ? AND ranking = ? LIMIT 8",
           (f"7d:{J}", "searches:no-result"))
C.note("pour le merchandising : fautes de frappe à corriger (synonymes), produits demandés mais absents")

# -----------------------------------------------------------------------------
C.titre("Ce que Cassandra refuse, et pourquoi le modèle a une table par requête")
refus = [
    ("Vues de tous les produits un jour donné, sans clé de partition",
     "SELECT sku, views FROM product_daily_counts WHERE day = ?", (J,)),
    ("Trier une partition sur une colonne qui n'est pas de clustering",
     "SELECT sku, views FROM product_views_by_day WHERE day = ? AND shard = ? ORDER BY views DESC", (J, 0)),
    ("Grouper sur une colonne hors clé primaire",
     "SELECT category, count(*) FROM rankings WHERE period = ? AND ranking = ? GROUP BY category",
     (SEMAINE, "products:all")),
]
for intitule, cql, params in refus:
    print(f"\n   {intitule} :")
    C.montrer(cql, params)
    try:
        s.execute(s.prepare(cql), params)
        print("   (acceptée)")
    except InvalidRequest as e:
        print("   REFUSÉE : " + str(e).split("message=")[-1].strip('"}').split(". ")[0])
C.note("la première lirait tout le cluster (ALLOW FILTERING) ; les deux autres demandent un tri ou un "
       "agrégat que seul un job peut précalculer")
