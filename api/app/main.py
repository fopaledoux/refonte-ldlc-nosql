"""
Refonte LDLC — prototype d'API (bonus du projet)

Une API web sans état, comme les serveurs web de l'architecture : chaque route passe par
la base faite pour elle.
  - Catalogue           -> MongoDB  (Lot 1)
  - Session et panier   -> Redis    (Lot 2)
  - Vues, classements, commandes, prix -> Cassandra (Lot 3)
  - Recommandations et compatibilités  -> Neo4j     (Lot 4)
Documentation interactive : http://localhost:8000/docs
"""
import time

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from . import analytics, catalogue, recommandations, session
from .bases import cache, cassandra, graphe, mongo

app = FastAPI(
    title="Refonte LDLC : prototype d'API",
    version="0.2.0",
    description=(
        "Prototype de l'API web de la refonte : chaque route utilise la base de son lot. "
        "**Catalogue** : MongoDB (Lot 1). **Session et panier** : Redis (Lot 2), en-tête `X-Session-Id` "
        "obtenu par `POST /sessions`. **Analytics** : Cassandra (Lot 3). **Recommandations** : Neo4j (Lot 4). "
        "Les montants sont des chaînes, jamais des flottants."
    ),
)
app.include_router(catalogue.router)
app.include_router(session.router)
app.include_router(analytics.router)
app.include_router(recommandations.router)


@app.get("/", include_in_schema=False)
def accueil():
    return RedirectResponse("/docs")


@app.get("/health", tags=["Santé"], summary="État et temps de réponse des quatre bases")
def sante():
    etats = {}
    for nom, verifier in (("mongodb", lambda: mongo().command("ping")),
                          ("redis", lambda: cache().ping()),
                          ("cassandra", lambda: cassandra().execute("SELECT release_version FROM system.local").one()),
                          ("neo4j", lambda: graphe("RETURN 1 AS ok"))):
        debut = time.perf_counter()
        try:
            verifier()
            etats[nom] = {"ok": True, "ms": round((time.perf_counter() - debut) * 1000, 2)}
        except Exception as e:                                # l'état d'une base n'empêche pas de lire les autres
            etats[nom] = {"ok": False, "error": str(e)[:200]}
    return {"ok": all(e["ok"] for e in etats.values()), "databases": etats}
