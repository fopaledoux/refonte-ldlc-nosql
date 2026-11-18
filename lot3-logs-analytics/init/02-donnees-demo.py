"""
Lot 3 — Données de démonstration, générées de façon déterministe (graine 20261117)
et datées par rapport au moment du chargement (J = aujourd'hui, en UTC) :

  - 7 jours d'événements bruts (de J-6 à maintenant), issus de visites simulées
    sur les 16 222 produits en vente du Lot 1 ;
  - 28 jours de compteurs de vues et d'ajouts au panier (les 7 derniers jours
    sont exactement le décompte des événements bruts) ;
  - la sortie d'une carte graphique (GPU-000009, GeForce RTX 5090) à J-2, 10 h
    heure de Paris : le cas de la partition chaude ;
  - 12 mois de commandes, composées comme de vrais paniers (montages PC aux
    pièces compatibles, évolutions, périphériques), et d'historique des prix.

Échelle : environ 2 500 visites par jour, soit 1/300 du trafic visé (5 millions
d'événements par jour). Les projections de volume partent des tailles mesurées.
"""
import bisect
import random
import sys
import time as chrono
import unicodedata
import uuid
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, "/lot3/lib")
import commun as C  # noqa: E402

GRAINE = 20261117
rng = random.Random(GRAINE)       # réinitialisé au début de chaque phase (jour, prix, commandes)
MAINTENANT = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
J = MAINTENANT.date()
MINUIT = datetime.combine(J, time(0))   # repère des dates passées : indépendant de l'heure du chargement
JOURS_BRUTS = 7
JOURS_COMPTEURS = 28
VISITES_PAR_JOUR = 2500
SKU_SORTIE = "GPU-000009"
SORTIE = datetime.combine(J - timedelta(days=2), time(9, 0))   # 10 h à Paris (UTC+1)

# Trafic par heure UTC (creux la nuit, pics à midi et en soirée, heure de Paris = UTC+1)
POIDS_HEURE = [1.0, 0.6, 0.4, 0.3, 0.3, 0.5, 1.0, 2.0, 3.0, 3.6, 4.0, 4.6,
               4.8, 4.2, 4.0, 4.2, 4.4, 4.6, 5.0, 5.6, 6.0, 5.2, 3.6, 2.0]
# Lundi ... dimanche
POIDS_JOUR = [1.0, 0.95, 1.0, 0.95, 0.9, 1.05, 1.2]
POIDS_CATEGORIE = {
    "cartes-graphiques": 4, "processeurs": 3, "ecrans": 2.5, "cartes-meres": 2,
    "souris": 1.5, "claviers": 1.5, "disques-internes": 1.5, "memoire-ram": 1.2,
    "casques-audio": 1.2, "boitiers": 1.2, "alimentations": 1, "ventirads": 1,
    "disques-externes": 0.8, "webcams": 0.8, "systemes-exploitation": 0.8,
    "enceintes": 0.6, "pates-thermiques": 0.6, "ventilateurs-boitier": 0.5,
    "cartes-wifi": 0.5, "onduleurs": 0.4, "cartes-reseau": 0.4, "cartes-son": 0.4,
    "controleurs-ventilateurs": 0.3, "lecteurs-optiques": 0.2,
}
# Termes de recherche et leur fréquence relative. Les derniers ne correspondent à
# aucun produit (fautes de frappe, produits non vendus) : c'est ce que la
# recherche « sans résultat » (Q9) doit faire remonter.
TERMES = [
    ("rtx 5090", 30), ("9800x3d", 16), ("rtx 5080", 18), ("rtx 5070 ti", 14), ("rtx 5070", 12),
    ("rx 9070 xt", 10), ("ryzen 7", 12), ("ryzen 9", 8), ("i5 14600k", 6), ("ddr5 32 go", 10),
    ("ssd nvme 2 to", 8), ("samsung 990 pro", 6), ("ecran 27", 10), ("ecran 240 hz", 6),
    ("ecran oled", 5), ("souris logitech", 8), ("clavier corsair", 6), ("casque steelseries", 5),
    ("alimentation 850", 7), ("carte mere b650", 7), ("carte mere z790", 5), ("boitier corsair", 4),
    ("ventirad noctua", 5), ("pate thermique", 4), ("onduleur apc", 3), ("webcam logitech", 4),
    ("be quiet", 4), ("asus rog", 6), ("windows 11", 4), ("disque dur externe", 3),
    ("rtx 5900", 4), ("ps5", 5), ("ryzn 7", 2), ("iphone 17", 3), ("carte grafique", 2),
    ("steam deck", 2), ("macbook air", 2),
]


def normaliser(texte):
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return " ".join(sans_accents.lower().split())


def arrondi(x):
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def nouvel_uuid():
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def ttl(ts):
    """TTL relatif à la date de l'événement : il expire 90 jours après avoir eu lieu,
    pas 90 jours après son chargement."""
    return max(1, C.TTL_EVENEMENTS - int((MAINTENANT - ts).total_seconds()))


# --- Catalogue (export du Lot 1) et popularité -------------------------------
def lire_produits():
    produits = []
    with open("/lot3/data/produits.tsv", encoding="utf-8") as f:
        next(f)
        for ligne in f:
            (sku, cat, marque, prix, avis, nom, mots,
             socket, memoire, format_carte, formats_boitier, _puce) = ligne.rstrip("\n").split("\t")
            texte = normaliser(" ".join((nom, marque, cat, mots)))
            boost = 3 if "geforce rtx 50" in texte else 1
            poids = POIDS_CATEGORIE.get(cat, 0.5) * (1 + int(avis)) ** 0.35 * rng.lognormvariate(0, 1.0) * boost
            produits.append({"sku": sku, "cat": cat, "marque": marque, "nom": nom, "prix": Decimal(prix),
                             "texte": texte, "poids": poids,
                             # attributs de compatibilité (Lot 1), pour composer des paniers réalistes
                             "socket": socket or None, "memoire": memoire or None, "format": format_carte or None,
                             "formats": formats_boitier.split("|") if formats_boitier else []})
    return produits


class Tirage:
    """Tirage pondéré en O(log n) sur une liste fixe."""

    def __init__(self, elements, poids):
        self.elements, self.cumul, total = elements, [], 0.0
        for p in poids:
            total += p
            self.cumul.append(total)

    def un(self):
        return self.elements[bisect.bisect(self.cumul, rng.random() * self.cumul[-1])]

    def plusieurs(self, k):
        return rng.choices(self.elements, cum_weights=self.cumul, k=k)


produits = lire_produits()
PAR_SKU = {p["sku"]: p for p in produits}
TOUS = Tirage([p["sku"] for p in produits], [p["poids"] for p in produits])
par_cat = defaultdict(list)
for p in produits:
    par_cat[p["cat"]].append(p)
CATS = sorted(par_cat)
TIRAGE_CAT = Tirage(CATS, [sum(p["poids"] for p in par_cat[c]) for c in CATS])
DANS_CAT = {c: Tirage([p["sku"] for p in par_cat[c]], [p["poids"] for p in par_cat[c]]) for c in CATS}


def tirages_par(categorie, cles):
    """Tirage pondéré par valeur d'attribut : cles(produit) -> valeurs (socket, type de mémoire...)."""
    groupes = defaultdict(list)
    for p in par_cat[categorie]:
        for cle in cles(p):
            groupes[cle].append(p)
    return {cle: Tirage([p["sku"] for p in g], [p["poids"] for p in g]) for cle, g in groupes.items()}


# Compatibilité, pour les montages : même socket, même mémoire, format accepté par le boîtier
CARTES_PAR_SOCKET = tirages_par("cartes-meres", lambda p: [p["socket"]] if p["socket"] and p["memoire"] else [])
MEMOIRE_PAR_TYPE = tirages_par("memoire-ram", lambda p: [p["memoire"]] if p["memoire"] else [])
BOITIERS_PAR_FORMAT = tirages_par("boitiers", lambda p: p["formats"])
_cpus = [p for p in par_cat["processeurs"] if p["socket"] in CARTES_PAR_SOCKET]
CPU_MONTAGE = Tirage([p["sku"] for p in _cpus], [p["poids"] for p in _cpus])
PERIPHERIQUES = ["souris", "claviers", "casques-audio", "ecrans", "webcams", "enceintes"]
PAR_CAT_MARQUE = {}


def meme_marque(categorie, marque):
    cle = (categorie, marque)
    if cle not in PAR_CAT_MARQUE:
        g = [p for p in par_cat[categorie] if p["marque"] == marque]
        PAR_CAT_MARQUE[cle] = Tirage([p["sku"] for p in g], [p["poids"] for p in g]) if g else None
    return PAR_CAT_MARQUE[cle]

RECHERCHES = {}
for terme, _ in TERMES:
    jetons = terme.split()
    trouves = [p for p in produits if all(j in p["texte"] for j in jetons)]
    RECHERCHES[terme] = (len(trouves), Tirage([p["sku"] for p in trouves], [p["poids"] for p in trouves]) if trouves else None)
TIRAGE_TERME = Tirage([t for t, _ in TERMES], [w for _, w in TERMES])


# --- Visites simulées ----------------------------------------------------------
def simuler_visite(jour):
    """Une visite complète. Les événements postérieurs à MAINTENANT sont écartés par l'appelant :
    les tirages ne dépendent jamais de l'heure du chargement."""
    heure = rng.choices(range(24), POIDS_HEURE)[0]
    t = datetime.combine(jour, time(heure)) + timedelta(seconds=rng.uniform(0, 3599))
    visite = nouvel_uuid()
    appareil = rng.choices(("desktop", "mobile", "app"), (55, 38, 7))[0]
    client = f"CUST-{rng.randint(1, 25000):06d}" if rng.random() < 0.35 else None
    cat = TIRAGE_CAT.un()
    fan_sortie = t >= SORTIE and t < SORTIE + timedelta(days=1) and rng.random() < 0.35
    n = min(40, 1 + int(rng.expovariate(1 / 5)))
    evts, dernier_sku, prochain = [], None, None

    def ajouter(type_, sku=None, page=None, requete=None, resultats=None, cible=None):
        evts.append((t, nouvel_uuid(), type_, client, sku, page, requete, resultats, cible, None))

    ajouter("page_view", page="/" if rng.random() < 0.4 else f"/{cat}/")
    for _ in range(n - 1):
        t += timedelta(seconds=rng.uniform(4, 110))
        r = rng.random()
        if prochain or (fan_sortie and r < 0.45) or r < 0.50:
            sku = prochain or (SKU_SORTIE if fan_sortie and r < 0.45 else DANS_CAT[cat].un())
            prochain, dernier_sku = None, sku
            ajouter("product_view", sku=sku, page=f"/fiche/{sku}")
        elif r < 0.63:
            terme = TIRAGE_TERME.un()
            nb, tirage = RECHERCHES[terme]
            ajouter("search", page="/recherche/", requete=terme, resultats=nb)
            if tirage and rng.random() < 0.6:
                prochain = tirage.un()
        elif r < 0.80:
            if rng.random() < 0.2:
                cat = TIRAGE_CAT.un()
            ajouter("page_view", page=f"/{cat}/")
        else:
            cible = rng.choices(("add_to_cart", "compare", "filter", "reco", "wishlist"), (35, 15, 25, 15, 10))[0]
            if dernier_sku is None:
                cible = "filter"
            ajouter("click", sku=None if cible == "filter" else dernier_sku, page=f"/{cat}/", cible=cible)
    return visite, appareil, evts


INS_HEURE = ("INSERT INTO events_by_hour (day, hour, bucket, ts, event_id, type, visit_id, customer_id, "
             "sku, page, search_query, results, target, quantity, device) "
             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) USING TTL ?")
INS_VISITE = ("INSERT INTO events_by_visit (visit_id, ts, event_id, device, type, customer_id, sku, page, "
              "search_query, results, target) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) USING TTL ?")
MAJ_PRODUIT = ("UPDATE product_daily_counts SET views = views + ?, cart_adds = cart_adds + ? "
               "WHERE sku = ? AND day = ?")
MAJ_JOUR = "UPDATE product_views_by_day SET views = views + ? WHERE day = ? AND shard = ? AND sku = ?"
INS_CMD_CLIENT = ("INSERT INTO orders_by_customer (customer_id, ordered_at, order_id, status, total, currency, "
                  "item_count, lines) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
INS_CMD_ID = ("INSERT INTO orders_by_id (order_id, customer_id, ordered_at, status, status_history, total, "
              "currency, lines) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
INS_PRIX = ("INSERT INTO price_history_by_product (sku, changed_at, price, regular_price, currency, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)")


def compte_rendu(nom, resultat):
    lignes, requetes, duree = resultat
    print(f"       {nom:<26} {lignes:>8} lignes  {requetes:>6} requêtes  {duree:5.1f} s"
          f"  ({lignes / max(duree, 0.001):,.0f} lignes/s)".replace(",", " "))


def charger_evenements(chargeur, vues, paniers):
    print(f"[lot3]   Événements bruts : {JOURS_BRUTS} jours, environ {VISITES_PAR_JOUR} visites par jour,"
          f" sortie de {SKU_SORTIE} le {SORTIE:%Y-%m-%d} à {SORTIE:%H:%M} UTC")
    total = [0, 0, 0.0]
    nb_visites = 0
    for decalage in range(JOURS_BRUTS - 1, -1, -1):
        jour = J - timedelta(days=decalage)
        rng.seed(GRAINE * 100 + decalage)            # une graine par jour relatif : J-3 est toujours le même
        facteur = POIDS_JOUR[jour.weekday()] * (1.6 if jour == SORTIE.date() else 1)
        par_heure, par_visite = [], []
        for _ in range(int(VISITES_PAR_JOUR * facteur)):
            visite, appareil, evts = simuler_visite(jour)
            evts = [e for e in evts if e[0] <= MAINTENANT]     # aujourd'hui : rien après maintenant
            if not evts:
                continue
            nb_visites += 1
            for (ts, eid, type_, client, sku, page, req, res, cible, qte) in evts:
                par_heure.append((ts.date(), ts.hour, C.bucket(eid), ts, eid, type_, visite, client, sku,
                                  page, req, res, cible, qte, appareil, ttl(ts)))
                par_visite.append((visite, ts, eid, appareil, type_, client, sku, page, req, res, cible, ttl(ts)))
                if type_ == "product_view":
                    vues[(sku, ts.date())] += 1
                elif cible == "add_to_cart":
                    paniers[(sku, ts.date())] += 1
        for table, res in (
                ("events_by_hour", chargeur.ecrire(C.lots_par_partition(INS_HEURE, par_heure, lambda r: r[:3]))),
                ("events_by_visit", chargeur.ecrire(C.lots_par_partition(INS_VISITE, par_visite, lambda r: r[0])))):
            for i in range(3):
                total[i] += res[i]
        print(f"       {jour}  {len(par_heure):>6} événements")
    compte_rendu("événements (2 tables)", total)
    return nb_visites


def completer_compteurs(vues, paniers):
    """Jours plus anciens que le brut : vues tirées selon la même popularité."""
    jours_complets = [J - timedelta(days=d) for d in range(1, JOURS_BRUTS) if J - timedelta(days=d) != SORTIE.date()]
    base = sum(n for (s, j), n in vues.items() if j in jours_complets) / sum(
        POIDS_JOUR[j.weekday()] for j in jours_complets)
    for decalage in range(JOURS_BRUTS, JOURS_COMPTEURS):
        jour = J - timedelta(days=decalage)
        rng.seed(GRAINE * 100 + decalage)
        nb = int(base * POIDS_JOUR[jour.weekday()] * rng.uniform(0.93, 1.07))
        for sku, n in Counter(TOUS.plusieurs(nb)).items():
            vues[(sku, jour)] += n
            ajouts = sum(1 for _ in range(n) if rng.random() < 0.05)
            if ajouts:
                paniers[(sku, jour)] += ajouts


def charger_compteurs(chargeur, vues, paniers):
    lignes_produit = [(n, paniers.get((sku, jour), 0), sku, jour) for (sku, jour), n in vues.items()]
    lignes_jour = [(n, jour, C.shard(sku), sku) for (sku, jour), n in vues.items()]
    print(f"[lot3]   Compteurs : {JOURS_COMPTEURS} jours, {sum(vues.values())} vues, "
          f"{sum(paniers.values())} ajouts au panier")
    compte_rendu("product_daily_counts", chargeur.ecrire(
        C.lots_par_partition(MAJ_PRODUIT, lignes_produit, lambda r: r[2], max_lignes=28, compteur=True)))
    compte_rendu("product_views_by_day", chargeur.ecrire(
        C.lots_par_partition(MAJ_JOUR, lignes_jour, lambda r: (r[1], r[2]), max_lignes=100, compteur=True)))


# --- Historique des prix -------------------------------------------------------
# Écrit dans le Lot 3 par le connecteur des Change Streams du Lot 1 (un changement
# de price.amount = une ligne). Reconstruit ici sur 12 mois, à rebours depuis le prix
# courant du catalogue. MON-000351 suit un scénario fixe pour la requête Omnibus (Q8).
SCENARIOS_PRIX = {
    "MON-000351": [(365, "109.99", None, "initial"), (200, "99.99", None, "update"),
                   (60, "89.99", None, "update"), (20, "79.99", "89.99", "promo"),
                   (15, "89.99", None, "promo_end")],
}


def prix_a(historique, t):
    """Prix en vigueur à l'instant t (historique trié par date croissante)."""
    i = bisect.bisect_right([h[0] for h in historique], t) - 1
    return historique[max(i, 0)][1]


def historique_prix(p):
    debut = MINUIT - timedelta(days=365)
    if p["sku"] in SCENARIOS_PRIX:
        return [(MINUIT - timedelta(days=j) + timedelta(hours=3), Decimal(prix), Decimal(reg) if reg else None, raison)
                for j, prix, reg, raison in SCENARIOS_PRIX[p["sku"]]]
    dates = sorted(debut + timedelta(seconds=rng.uniform(86400, 363 * 86400))
                   for _ in range(rng.choices(range(7), (10, 20, 22, 18, 13, 10, 7))[0]))
    prix = [p["prix"]]
    for _ in dates:   # à rebours : le matériel informatique baisse en général
        prix.append(arrondi(prix[-1] * Decimal(str(min(1.4, max(0.75, rng.gauss(1.04, 0.06)))))))
    prix.reverse()
    hist = [(debut, prix[0], None, "initial")] + [(d, prix[i + 1], None, "update") for i, d in enumerate(dates)]
    for _ in range(rng.choices((0, 1, 2), (55, 33, 12))[0]):
        for _essai in range(5):
            t0 = debut + timedelta(days=rng.uniform(2, 355))
            t1 = t0 + timedelta(days=rng.randint(3, 10))
            if t1 >= MINUIT - timedelta(days=1) or any(
                    t0 - timedelta(days=1) <= h[0] <= t1 + timedelta(days=1) for h in hist[1:]):
                continue
            regulier = prix_a(hist, t0)
            remise = Decimal(str(round(rng.uniform(0.08, 0.20), 2)))
            hist += [(t0, arrondi(regulier * (1 - remise)), regulier, "promo"), (t1, regulier, None, "promo_end")]
            hist.sort(key=lambda h: h[0])
            break
    return hist


# --- Commandes -----------------------------------------------------------------
# Écrites dans le Lot 3 à partir des événements du système de gestion des commandes
# (la source de vérité). 12 mois, 25 000 clients ; CUST-000042 (client des démos du
# Lot 2) a un historique fourni.
def composer_panier():
    """Le contenu d'une commande, selon l'intention d'achat. C'est ce qui donne au Lot 4 un
    vrai signal « achetés ensemble » : un processeur part avec une carte mère compatible."""
    intention = rng.choices(("montage", "evolution", "peripheriques", "simple"), (22, 18, 20, 40))[0]
    if intention == "montage":                       # PC complet, pièces compatibles entre elles
        cpu = CPU_MONTAGE.un()
        carte = CARTES_PAR_SOCKET[PAR_SKU[cpu]["socket"]].un()
        skus = [cpu, carte]
        if PAR_SKU[carte]["memoire"] in MEMOIRE_PAR_TYPE:
            skus.append(MEMOIRE_PAR_TYPE[PAR_SKU[carte]["memoire"]].un())
        if PAR_SKU[carte]["format"] in BOITIERS_PAR_FORMAT and rng.random() < 0.8:
            skus.append(BOITIERS_PAR_FORMAT[PAR_SKU[carte]["format"]].un())
        for cat, proba in (("cartes-graphiques", 0.5), ("alimentations", 0.6), ("disques-internes", 0.5),
                           ("ventirads", 0.35)):
            if rng.random() < proba:
                skus.append(DANS_CAT[cat].un())
        return [(s, 1) for s in dict.fromkeys(skus)]
    if intention == "evolution":                     # mise à niveau : carte graphique (+ alimentation), SSD...
        cat = rng.choices(("cartes-graphiques", "disques-internes", "memoire-ram", "ventirads"), (50, 25, 15, 10))[0]
        skus = [DANS_CAT[cat].un()]
        if cat == "cartes-graphiques" and rng.random() < 0.45:
            skus.append(DANS_CAT["alimentations"].un())
        return [(s, 1) for s in dict.fromkeys(skus)]
    if intention == "peripheriques":                 # poste de jeu, souvent de la même marque
        cats = rng.sample(PERIPHERIQUES, rng.choice((2, 2, 3)))
        premier = DANS_CAT[cats[0]].un()
        skus = [premier]
        for cat in cats[1:]:
            tirage = meme_marque(cat, PAR_SKU[premier]["marque"]) if rng.random() < 0.5 else None
            skus.append((tirage or DANS_CAT[cat]).un())
        return [(s, 1) for s in dict.fromkeys(skus)]
    skus = dict.fromkeys(TOUS.plusieurs(rng.choices((1, 2, 3), (70, 22, 8))[0]))   # achats isolés
    return [(s, rng.choices((1, 2, 3), (85, 12, 3))[0]) for s in skus]


def generer_commandes(historiques):
    brutes = []
    for c in range(1, 25001):
        client = f"CUST-{c:06d}"
        if client == "CUST-000042":
            n = 14
        elif rng.random() < 0.005:
            n = rng.randint(15, 60)          # comptes professionnels
        elif rng.random() < 0.35:
            n = 1 + int(rng.expovariate(1 / 1.6))
        else:
            continue
        for _ in range(n):
            jour = J - timedelta(days=rng.randint(0, 364))
            t = datetime.combine(jour, time(rng.choices(range(24), POIDS_HEURE)[0])) + timedelta(
                seconds=rng.uniform(0, 3599))
            if t <= MAINTENANT:
                brutes.append((t, client))
    brutes.sort()
    par_client, par_id = [], []
    for num, (t, client) in enumerate(brutes, start=1):
        commande = f"ORD-{num:08d}"
        lignes = []
        for sku, qte in composer_panier():
            lignes.append((sku, PAR_SKU[sku]["nom"], qte, prix_a(historiques[sku], t)))
        total = sum(q * pu for _, _, q, pu in lignes)
        # Tous les tirages d'abord, les conditions ensuite : le résultat ne dépend pas de l'heure du chargement
        annulee, h_annul, h_exp, h_liv, retour, j_ret = (rng.random() < 0.03, rng.uniform(1, 20), rng.uniform(20, 30),
                                                          rng.uniform(48, 80), rng.random() < 0.02, rng.uniform(8, 13))
        etapes = {t: "paid"}
        for delai, etape, possible in ((timedelta(hours=h_annul), "cancelled", annulee),
                                       (timedelta(hours=h_exp), "shipped", not annulee),
                                       (timedelta(hours=h_liv), "delivered", not annulee),
                                       (timedelta(days=j_ret), "returned", not annulee and retour)):
            if possible and t + delai <= MAINTENANT:
                etapes[t + delai] = etape
        statut = etapes[max(etapes)]
        par_client.append((client, t, commande, statut, total, "USD", sum(q for _, _, q, _ in lignes), lignes))
        par_id.append((commande, client, t, statut, etapes, total, "USD", lignes))
    return par_client, par_id


def main():
    debut = chrono.time()
    chargeur = C.Chargeur(processus=3)
    vues, paniers = Counter(), Counter()

    nb_visites = charger_evenements(chargeur, vues, paniers)
    print(f"       {nb_visites} visites simulées")
    completer_compteurs(vues, paniers)
    charger_compteurs(chargeur, vues, paniers)

    rng.seed(GRAINE * 100 + 80)
    historiques = {p["sku"]: historique_prix(p) for p in produits}
    lignes_prix = [(sku, t, prix, reg, "USD", raison) for sku, h in historiques.items() for (t, prix, reg, raison) in h]
    print(f"[lot3]   Historique des prix : {len(lignes_prix)} changements sur 12 mois")
    compte_rendu("price_history_by_product", chargeur.ecrire(
        C.lots_par_partition(INS_PRIX, lignes_prix, lambda r: r[0], coherence="quorum")))

    rng.seed(GRAINE * 100 + 90)
    par_client, par_id = generer_commandes(historiques)
    print(f"[lot3]   Commandes : {len(par_id)} sur 12 mois, "
          f"{len({c[0] for c in par_client})} clients")
    compte_rendu("orders_by_customer", chargeur.ecrire(
        C.lots_par_partition(INS_CMD_CLIENT, par_client, lambda r: r[0], max_lignes=10, coherence="quorum")))
    compte_rendu("orders_by_id", chargeur.ecrire(
        C.lots_par_partition(INS_CMD_ID, par_id, lambda r: r[0], coherence="quorum")))

    chargeur.fermer()
    print(f"[lot3]   Données chargées en {chrono.time() - debut:.0f} s")


if __name__ == "__main__":
    main()
