"""
Lot 3 — Requêtes types en écriture (W1 à W6). Rejouables : chaque exécution
nettoie ce qu'elle a créé, sauf les compteurs (qu'on ne remet jamais à zéro) et
un événement de démonstration par exécution, qui expire au bout de 90 jours.

  docker compose exec cassandra python3 /lot3/queries/02-ecritures.py
"""
import sys
import uuid
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, "/lot3/lib")
sys.path.insert(0, "/lot3/connecteurs")
import commun as C  # noqa: E402
import panier       # noqa: E402

_, s = C.connecter()
MAINTENANT = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
J = MAINTENANT.date()
SKU_TEST = "TEST-000001"                           # absent du catalogue : jamais classé par le job
VISITE = uuid.UUID("dddddddd-0000-4000-8000-000000000001")
EVT = uuid.uuid5(uuid.NAMESPACE_URL, f"ldlc-demo/{MAINTENANT.isoformat()}")


def vues_test():
    return s.execute("SELECT views FROM product_daily_counts WHERE sku = %s AND day = %s", (SKU_TEST, J)).one()


def un_clic():
    """Le travail du service d'ingestion pour un événement : 2 insertions, 2 incréments."""
    C.ecrire(s, "INSERT INTO events_by_hour (day, hour, bucket, ts, event_id, type, visit_id, customer_id, sku, "
                "page, device) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
             (J, MAINTENANT.hour, C.bucket(EVT), MAINTENANT, EVT, "product_view", VISITE, "CUST-000042",
              SKU_TEST, f"/fiche/{SKU_TEST}", "desktop"))
    C.ecrire(s, "INSERT INTO events_by_visit (visit_id, ts, event_id, device, type, customer_id, sku, page) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
             (VISITE, MAINTENANT, EVT, "desktop", "product_view", "CUST-000042", SKU_TEST, f"/fiche/{SKU_TEST}"))
    C.ecrire(s, "UPDATE product_daily_counts SET views = views + 1 WHERE sku = ? AND day = ?", (SKU_TEST, J))
    C.ecrire(s, "UPDATE product_views_by_day SET views = views + 1 WHERE day = ? AND shard = ? AND sku = ?",
             (J, C.shard(SKU_TEST), SKU_TEST))


# -----------------------------------------------------------------------------
C.titre("W1. Un clic sur une fiche produit : le chemin d'écriture",
        "cohérence LOCAL_ONE : un seul réplica confirme, les autres reçoivent la copie en arrière-plan")
avant = vues_test()
un_clic()
C.note(f"bucket = event_id modulo 16 = {C.bucket(EVT)} ; shard = CRC32('{SKU_TEST}') modulo 16 = {C.shard(SKU_TEST)}")
C.note("en production, les 2 incréments sont agrégés sur quelques secondes : +734 en une écriture, pas 734 écritures")
C.executer(s, "SELECT type, sku, TTL(type) AS ttl_s, WRITETIME(type) AS writetime_us FROM events_by_visit "
              "WHERE visit_id = ? AND ts = ? AND event_id = ?", (VISITE, MAINTENANT, EVT))
C.note("aucun USING TTL dans l'INSERT : le default_time_to_live de la table (90 jours = 7 776 000 s) s'applique")

# -----------------------------------------------------------------------------
C.titre("W2. Le même événement livré deux fois (livraison « au moins une fois »)",
        "le journal est idempotent, le compteur ne l'est pas")
un_clic()
C.executer(s, "SELECT count(*) AS lignes FROM events_by_visit WHERE visit_id = ? AND ts = ? AND event_id = ?",
           (VISITE, MAINTENANT, EVT))
apres = vues_test()
C.note(f"journal : toujours 1 ligne (même clé primaire = même ligne, réécrite). Compteur : "
       f"{avant.views if avant else 0} -> {apres.views} pour un seul clic réel")
C.note("d'où l'agrégation par lot et l'acquittement après écriture (connecteur W6) : surcomptage borné à un lot")

# -----------------------------------------------------------------------------
C.titre("W3. Champ absent : null ou UNSET ?",
        "écrire null crée une tombstone par cellule, que chaque lecture devra ensuite parcourir")
VISITE_NULL = uuid.UUID("dddddddd-0000-4000-8000-000000000002")
TS_A, TS_B = datetime(2026, 1, 1, 0, 0, 0), datetime(2026, 1, 1, 0, 0, 1)
cql = ("INSERT INTO events_by_visit (visit_id, ts, event_id, type, customer_id, sku, search_query, results, target) "
       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
C.montrer(cql, (VISITE_NULL, TS_A, uuid.UUID(int=1), "page_view", None, None, None, None, None))
s.execute(s.prepare(cql), (VISITE_NULL, TS_A, uuid.UUID(int=1), "page_view", None, None, None, None, None))
print("   OK : 5 valeurs liées à None (null)")
C.montrer(cql.replace(", customer_id, sku, search_query, results, target) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      ") VALUES (?, ?, ?, ?)"), (VISITE_NULL, TS_B, uuid.UUID(int=2), "page_view"))
s.execute(s.prepare(cql), C.sans_null((VISITE_NULL, TS_B, uuid.UUID(int=2), "page_view", None, None, None, None, None)))
print("   OK : 5 valeurs envoyées en UNSET (le pilote ne transmet rien pour ces colonnes)")
C.executer(s, "SELECT ts, type, sku FROM events_by_visit WHERE visit_id = ?", (VISITE_NULL,), tracer=True)
C.note("les quelque 92 000 événements de démonstration ont environ 5 champs vides chacun : envoyés en null, "
       "ils auraient écrit près de 460 000 tombstones")

# -----------------------------------------------------------------------------
C.titre("W4. Une commande passée, puis expédiée : deux tables écrites ensemble",
        "BATCH journalisé (LOGGED) : les deux écritures aboutiront toutes les deux, même si le coordinateur tombe")
COMMANDE, CLIENT = "ORD-99000001", "CUST-000042"
OrderLine = namedtuple("order_line", "sku name quantity unit_price")    # le type order_line du schéma
lignes = [OrderLine("CPU-000001", "AMD Ryzen 7 9800X3D", 1, Decimal("451.50")),
          OrderLine("MB-000131", "MSI PRO B760-P WIFI DDR4", 1, Decimal("159.95"))]
total = sum(q * p for _, _, q, p in lignes)
C.lot(s, [
    ("INSERT INTO orders_by_customer (customer_id, ordered_at, order_id, status, total, currency, item_count, lines) "
     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (CLIENT, MAINTENANT, COMMANDE, "paid", total, "USD", 2, lignes)),
    ("INSERT INTO orders_by_id (order_id, customer_id, ordered_at, status, status_history, total, currency, lines) "
     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (COMMANDE, CLIENT, MAINTENANT, "paid", {MAINTENANT: "paid"}, total, "USD", lignes)),
])
C.executer(s, "SELECT ordered_at, order_id, status, total FROM orders_by_customer WHERE customer_id = ? LIMIT 2",
           (CLIENT,), profil="quorum")
C.note("lue en LOCAL_QUORUM après une écriture en LOCAL_QUORUM : 2 + 2 > 3 réplicas, le client voit sa commande")

print("\n   Expédition : l'événement ne porte que le numéro de commande.")
cmd = C.executer(s, "SELECT customer_id, ordered_at FROM orders_by_id WHERE order_id = ?", (COMMANDE,), profil="quorum")[0]
expedition = MAINTENANT + timedelta(seconds=1)
C.lot(s, [
    ("UPDATE orders_by_id SET status = ?, status_history = status_history + ? WHERE order_id = ?",
     ("shipped", {expedition: "shipped"}, COMMANDE)),
    ("UPDATE orders_by_customer SET status = ? WHERE customer_id = ? AND ordered_at = ? AND order_id = ?",
     ("shipped", cmd.customer_id, cmd.ordered_at, COMMANDE)),
])
C.executer(s, "SELECT status, status_history FROM orders_by_id WHERE order_id = ?", (COMMANDE,), profil="quorum")
C.executer(s, "SELECT order_id, status FROM orders_by_customer WHERE customer_id = ? AND ordered_at = ? AND order_id = ?",
           (CLIENT, cmd.ordered_at, COMMANDE), profil="quorum")
C.note("sans orders_by_id, impossible de retrouver ordered_at, qui fait partie de la clé de orders_by_customer")
C.lot(s, [("DELETE FROM orders_by_customer WHERE customer_id = ? AND ordered_at = ? AND order_id = ?",
           (CLIENT, cmd.ordered_at, COMMANDE)),
          ("DELETE FROM orders_by_id WHERE order_id = ?", (COMMANDE,))])
C.note("démo nettoyée (une suppression écrit aussi une tombstone : à réserver aux cas rares)")

# -----------------------------------------------------------------------------
C.titre("W5. Une promotion arrive du Lot 1 (Change Stream) : -15 % sur MON-000351",
        "avant de l'afficher, le prix de référence légal (Q8)")
PRODUIT = "MON-000351"
debut = datetime.combine(J, datetime.min.time()) - timedelta(days=30)
avant_fenetre = s.execute("SELECT price FROM price_history_by_product WHERE sku = %s AND changed_at <= %s LIMIT 1",
                          (PRODUIT, debut)).one().price
dans_fenetre = s.execute("SELECT min(price) AS p FROM price_history_by_product WHERE sku = %s AND changed_at > %s",
                         (PRODUIT, debut)).one().p
reference = min(p for p in (avant_fenetre, dans_fenetre) if p is not None)
regulier = s.execute("SELECT price FROM price_history_by_product WHERE sku = %s LIMIT 1", (PRODUIT,)).one().price
promo = (regulier * Decimal("0.85")).quantize(Decimal("0.01"))
C.ecrire(s, "INSERT INTO price_history_by_product (sku, changed_at, price, regular_price, currency, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)", (PRODUIT, MAINTENANT, promo, regulier, "USD", "promo"), profil="quorum")
C.executer(s, "SELECT changed_at, price, regular_price, reason FROM price_history_by_product WHERE sku = ? LIMIT 3",
           (PRODUIT,), profil="quorum")
C.note(f"prix barré annoncé {regulier} $, nouveau prix {promo} $ : -15 % affiché par le back-office")
C.note(f"référence légale = plus bas prix des 30 jours = {reference} $ : remise réelle "
       f"{100 * (reference - promo) / reference:.1f} %. Annoncer -15 % serait une pratique trompeuse")
C.ecrire(s, "DELETE FROM price_history_by_product WHERE sku = ? AND changed_at = ?", (PRODUIT, MAINTENANT),
         profil="quorum")
C.note("démo nettoyée (le Lot 1 rétablit aussi le prix dans E3)")

# -----------------------------------------------------------------------------
C.titre("W6. Ajouts au panier dans Redis (Lot 2), relayés par le connecteur",
        "Stream events:cart -> groupe de consommateurs lot3-cassandra -> Cassandra, puis XACK")
r = panier.Redis()
PANIER = "cart:guest:demo-lot3"
compteur = lambda: s.execute("SELECT cart_adds FROM product_daily_counts WHERE sku = 'CPU-000001' AND day = %s",
                             (J,)).one()
avant = compteur()
for sku, qte in (("CPU-000001", 2), ("MB-000131", 1)):
    print(f"redis> FCALL cart_add 4 {PANIER} catalog:sellable catalog:limits events:cart {sku} {qte} 604800 10")
    print("   " + str(r.cmd("FCALL", "cart_add", 4, PANIER, "catalog:sellable", "catalog:limits", "events:cart",
                             sku, qte, 604800, 10)))
panier.executer(session=s)
apres = compteur()
C.note(f"CPU-000001, ajouts au panier aujourd'hui : {avant.cart_adds if avant else 0} -> {apres.cart_adds}")
C.executer(s, "SELECT ts, type, sku, quantity FROM events_by_hour WHERE day = ? AND hour = ? AND bucket IN ? "
              "AND type = 'cart_add' ALLOW FILTERING", (J, MAINTENANT.hour, list(range(C.BUCKETS))))
C.note("ALLOW FILTERING borné aux 16 partitions de l'heure : acceptable pour un contrôle, pas pour une page web")
panier.executer(session=s)
C.note("relancé : rien à relire, tout a été acquitté")
r.cmd("DEL", PANIER)
