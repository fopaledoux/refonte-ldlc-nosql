"""
Lot 4 — Accès à Neo4j depuis Python, par l'API HTTP de requêtes (Query API, Neo4j 5.19 et plus)

Une requête Cypher et ses paramètres partent en JSON ; les lignes reviennent en JSON. Aucune
dépendance à installer : la bibliothèque standard suffit, dans n'importe quel conteneur Python
du dépôt. En production, le pilote officiel (protocole Bolt) apporte le pool de connexions et
le routage vers les membres d'un cluster ; ici, une instance unique.
"""
import json
import os
import time
import urllib.error
import urllib.request

URL = os.environ.get("NEO4J_URL", "http://localhost:7474") + "/db/neo4j/query/v2"


class ErreurCypher(Exception):
    pass


def cypher(requete, delai=300, **parametres):
    """Exécute une requête (transaction implicite) et renvoie ses lignes, en dictionnaires."""
    corps = json.dumps({"statement": requete, "parameters": parametres}).encode()
    req = urllib.request.Request(URL, data=corps, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=delai) as r:
            reponse = json.load(r)
    except urllib.error.HTTPError as e:
        erreurs = json.loads(e.read() or b"{}").get("errors") or [{"message": str(e)}]
        raise ErreurCypher("; ".join(f"{x.get('code', '')} {x['message']}".strip() for x in erreurs)) from None
    champs = reponse["data"]["fields"]
    return [dict(zip(champs, valeurs)) for valeurs in reponse["data"]["values"]]


def attendre(essais=30):
    """Neo4j répond-il ? (il démarre en une vingtaine de secondes)"""
    for essai in range(essais):
        try:
            return cypher("RETURN 1 AS ok")
        except (urllib.error.URLError, ConnectionError):
            if essai == essais - 1:
                raise
            time.sleep(2)
