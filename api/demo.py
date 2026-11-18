"""
Démonstration de bout en bout du prototype d'API : un parcours client qui traverse les
quatre bases, avec une vérification à chaque étape (le script s'arrête au premier écart).

  docker compose exec api python /api/demo.py

Le client de démonstration (CUST-099999) est hors de la plage des données générées :
son panier et ses sessions sont supprimés à la fin. Les vues enregistrées restent dans
Cassandra, comme toute vue réelle.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("API_URL", "http://localhost:8000")
CLIENT_DEMO = "CUST-099999"
etapes = 0


def appel(methode, chemin, corps=None, session=None, attendu=200):
    """Appelle l'API et vérifie le statut HTTP. Renvoie le JSON de la réponse."""
    donnees = json.dumps(corps).encode() if corps is not None else None
    requete = urllib.request.Request(BASE + chemin, data=donnees, method=methode)
    requete.add_header("Content-Type", "application/json")
    if session:
        requete.add_header("X-Session-Id", session)
    try:
        with urllib.request.urlopen(requete, timeout=15) as r:
            statut, contenu = r.status, r.read()
    except urllib.error.HTTPError as e:
        statut, contenu = e.code, e.read()
    reponse = json.loads(contenu) if contenu else None
    if statut != attendu:
        echec(f"{methode} {chemin} : statut {statut} au lieu de {attendu} -> {reponse}")
    return reponse


def verifier(condition, message):
    global etapes
    if not condition:
        echec(message)
    etapes += 1
    print(f"   OK  {message}")


def echec(message):
    print(f"   ÉCHEC  {message}")
    sys.exit(1)


def section(titre):
    print(f"\n== {titre}")


# -----------------------------------------------------------------------------------------
section("Santé des quatre bases")
sante = appel("GET", "/health")
verifier(sante["ok"], "MongoDB, Redis, Cassandra et Neo4j répondent : "
         + ", ".join(f"{n} {e['ms']} ms" for n, e in sante["databases"].items()))

# -----------------------------------------------------------------------------------------
section("Catalogue (MongoDB) : recherche, fiche, listing filtré, facettes")
resultats = appel("GET", "/search?" + urllib.parse.urlencode({"q": "ryzen 9800x3d"}))["items"]
cpu = next((p for p in resultats if p["sku"] == "CPU-000001"), None)
verifier(cpu is not None, f"Recherche « ryzen 9800x3d » : {len(resultats)} résultats, dont CPU-000001 ({cpu and cpu['name']})")

fiche = appel("GET", f"/products/{cpu['slug']}")
verifier(fiche["_id"] == "CPU-000001" and len(fiche["specs"]) > 0,
         f"Fiche {fiche['name']} en une lecture : {len(fiche['specs'])} caractéristiques, "
         f"{fiche['rating']['count']} avis, {len(fiche['compatibleProducts'])} produits compatibles")
vues_avant = fiche["views_today"]
vues_apres = appel("GET", f"/products/{cpu['slug']}")["views_today"]
verifier(vues_apres == vues_avant + 1, f"Chaque affichage est compté dans Cassandra : {vues_avant} -> {vues_apres} vues aujourd'hui")

filtres = [("spec", "socket:AM5"), ("spec", "core_count>=8"), ("spec", "tdp<=120"), ("max_price", "500"), ("limit", "4")]
vus, prix_precedent, page, apres = [], None, 0, None
while True:
    params = filtres + ([("after", apres)] if apres else [])
    reponse = appel("GET", "/categories/processeurs/products?" + urllib.parse.urlencode(params))
    for p in reponse["items"]:
        verifier_ordre = prix_precedent is None or float(p["price"]) >= prix_precedent
        if not verifier_ordre or p["sku"] in vus:
            echec(f"Pagination incohérente sur {p['sku']}")
        prix_precedent = float(p["price"])
        vus.append(p["sku"])
    page += 1
    apres = reponse["next"]
    if not apres:
        break
verifier(len(vus) > 0, f"Processeurs AM5, 8 cœurs ou plus, 120 W au plus, 500 USD au plus : {len(vus)} produits "
         f"sur {page} pages de 4, triés par prix, sans doublon (pagination par curseur)")

facettes = {f["key"]: f for f in appel("GET", "/categories/memoire-ram/facets")["facets"]}
ddr = facettes.get("ddr_generation")
verifier(ddr is not None, "Facettes de la mémoire : " + " · ".join(f"{v['value']} ({v['count']})" for v in ddr["values"]))

# -----------------------------------------------------------------------------------------
section("Session et panier (Redis)")
session = appel("POST", "/sessions", {"device": "desktop"}, attendu=201)
sid = session["session_id"]
verifier(session["session"]["cartKey"].startswith("cart:guest:"),
         f"Session ouverte ({sid[:8]}…), panier invité {session['session']['cartKey']}, expire après "
         f"{session['expires_in_s']} s d'inactivité")

ajout = appel("POST", "/cart/items", {"sku": "GPU-000009", "quantity": 5}, session=sid)
verifier(ajout["added"] == 2 and ajout["limit"] == 2 and ajout["limited"],
         f"RTX 5090 (GPU-000009) : 5 demandées, {ajout['added']} ajoutées, limite d'achat {ajout['limit']}")
refus = appel("POST", "/cart/items", {"sku": "CPU-000025", "quantity": 1}, session=sid, attendu=409)
verifier("NOTSELLABLE" in refus["detail"], f"Produit arrêté refusé : {refus['detail']}")
appel("POST", "/cart/items", {"sku": "CPU-000001", "quantity": 1}, session=sid)
panier = appel("GET", "/cart", session=sid)
somme = sum(float(l["line_total"]) for l in panier["lines"])
verifier(len(panier["lines"]) == 2 and abs(somme - float(panier["total"])) < 0.005,
         f"Panier : {panier['item_count']} articles, total {panier['total']} USD "
         "(nom et prix lus dans le catalogue, pas dans le panier)")
appel("PUT", "/cart/items/CPU-000001", {"quantity": 0}, session=sid)
verifier(len(appel("GET", "/cart", session=sid)["lines"]) == 1, "Quantité 0 : la ligne CPU-000001 est retirée")

connexion = appel("POST", "/sessions/login", {"customer_id": CLIENT_DEMO}, session=sid)
nouvelle = connexion["session_id"]
verifier(nouvelle != sid and connexion["merged_cart_lines"] == 1,
         f"Connexion de {CLIENT_DEMO} : nouvel identifiant de session, {connexion['merged_cart_lines']} ligne fusionnée")
appel("GET", "/cart", session=sid, attendu=401)
verifier(True, "L'ancien identifiant de session ne fonctionne plus (protection contre la fixation de session)")
panier = appel("GET", "/cart", session=nouvelle)
verifier(panier["cart_key"] == f"cart:customer:{CLIENT_DEMO}" and panier["lines"][0]["quantity"] == 2,
         f"Panier client {panier['cart_key']} : {panier['lines'][0]['name']} x {panier['lines'][0]['quantity']}")
appel("DELETE", "/cart", session=nouvelle)
appel("POST", "/sessions/logout", session=nouvelle)
appel("GET", "/sessions/me", session=nouvelle, attendu=401)
verifier(True, "Panier vidé et déconnexion : la session n'existe plus")

# -----------------------------------------------------------------------------------------
section("Analytics (Cassandra)")
vues = appel("GET", "/products/CPU-000001/views")
verifier(vues["today"]["views"] >= vues_apres and len(vues["last_7_days"]) >= 1,
         f"CPU-000001 : {vues['today']['views']} vues aujourd'hui, {len(vues['last_7_days'])} jours d'historique")
top = appel("GET", "/rankings/products?period=week&limit=3")
verifier(len(top["items"]) == 3 and top["items"][0]["rank"] == 1,
         f"Top de la semaine ({top['period']}) : " + ", ".join(f"{i['rank']}. {i['name']} ({i['views']})" for i in top["items"]))
page1 = appel("GET", "/customers/CUST-000042/orders?limit=3")
page2 = appel("GET", "/customers/CUST-000042/orders?" + urllib.parse.urlencode({"limit": 3, "page": page1["next"]}))
ids1, ids2 = [c["order_id"] for c in page1["orders"]], [c["order_id"] for c in page2["orders"]]
verifier(len(ids1) == 3 and not set(ids1) & set(ids2) and page1["orders"][-1]["ordered_at"] >= page2["orders"][0]["ordered_at"],
         f"Historique de CUST-000042, la plus récente d'abord : page 1 {ids1}, page 2 {ids2}")
commande = appel("GET", f"/orders/{ids1[0]}")
verifier(commande["customer_id"] == "CUST-000042",
         f"Commande {ids1[0]} : {commande['status']}, {commande['total']} USD, étapes "
         + " -> ".join(e["status"] for e in commande["status_history"]))
prix = appel("GET", "/products/MON-000351/price-history")
verifier(prix["reference_price_30_days"] == "79.99",
         f"MON-000351 : prix courant {prix['current_price']} USD, prix de référence légal (30 jours) "
         f"{prix['reference_price_30_days']} USD")

# -----------------------------------------------------------------------------------------
section("Recommandations (Neo4j)")
ensemble = appel("GET", "/products/CPU-000054/recommendations?limit=6")
cartes_am5 = {p["sku"] for p in appel("GET", "/products/CPU-000054/compatible?category=cartes-meres&limit=50")["items"]}
carte = next((p for p in ensemble["items"] if p["category"] == "cartes-meres"), None)
verifier(ensemble["source"] == "neo4j:BOUGHT_WITH" and carte is not None and carte["sku"] in cartes_am5,
         f"Souvent achetés avec le Ryzen 9 9900X3D : {len(ensemble['items'])} produits, dont la carte mère "
         f"{carte and carte['name']} ({carte and carte['orders_together']} paniers communs), au même socket AM5")
memoires = appel("GET", "/products/MB-000227/compatible?category=memoire-ram&limit=5")["items"]
verifier(len(memoires) == 5 and all(m["reason"] == "Mémoire DDR5" for m in memoires),
         f"Barrettes compatibles avec la carte MB-000227 (DDR5) : {len(memoires)} proposées, toutes en DDR5, "
         f"la première : {memoires[0]['name']}")

session = appel("POST", "/sessions", {"device": "desktop"}, attendu=201)["session_id"]
for sku in ("CPU-000054", "MB-000227"):
    appel("POST", "/cart/items", {"sku": sku, "quantity": 1}, session=session)
completer = appel("GET", "/cart/recommendations?limit=6", session=session)
fournies = {"socket:AM5", "memory:DDR5"}                       # ce que fournit la carte mère du panier
compatibles = all(set(i["requires"]) <= fournies | {"form:Mini ITX"} and
                  (not any(x.startswith("form:") for x in i["provides"]) or "form:Mini ITX" in i["provides"])
                  for i in completer["items"])
verifier(completer["items"] and compatibles and not {i["category"] for i in completer["items"]} & {"processeurs", "cartes-meres"},
         "Compléter le panier (processeur AM5 + carte Mini ITX DDR5) : "
         + ", ".join(f"{i['category']} {i['sku']}" for i in completer["items"]) + " ; tout est compatible avec le panier")
appel("DELETE", "/cart", session=session)

pour_moi = appel("GET", "/customers/CUST-000042/recommendations?limit=3")
verifier(len(pour_moi["items"]) > 0 and all(i["because_of"] for i in pour_moi["items"]),
         "Pour CUST-000042 : " + " ; ".join(f"{i['name']} (parce qu'il a acheté {i['because_of'][0]['name']})"
                                             for i in pour_moi["items"]))

print(f"\n{etapes} vérifications réussies.")
