#!/bin/sh
# =============================================================================
# Lot 4 — Chargement du graphe
# Exécuté par le conteneur ponctuel « neo4j-init » à chaque « docker compose up ».
#
# Comme les Lots 1 et 3, il ne recharge pas un graphe déjà chargé : les arêtes
# BOUGHT_WITH, calculées en dernier, servent de témoin.
#   Forcer le rechargement (graphe vidé, puis rechargé) :
#     docker compose run --rm -e FORCER=1 neo4j-init
#   Recalculer seulement les arêtes précalculées (le job nocturne) :
#     docker compose exec neo4j cypher-shell -f /lot4/init/04-precalculs.cypher
# =============================================================================
set -e
H="${NEO4J_HOST:-localhost}"
# cypher-shell est un programme Java : compilation rapide seulement, il ne vit que quelques secondes
export JAVA_OPTS="-XX:TieredStopAtLevel=1 -XX:+UseSerialGC"
CS="cypher-shell -a neo4j://$H:7687 --non-interactive"
debut=$(date +%s)

deja=$($CS --format plain "MATCH ()-[r:BOUGHT_WITH]->() RETURN count(r) > 0 AS ok" | tail -n 1)
if [ "$FORCER" != "1" ] && [ "$deja" = "TRUE" ]; then
  echo "[lot4] Graphe déjà chargé (volume neo4j-data) : rien à faire."
  echo "       Pour le recharger : docker compose run --rm -e FORCER=1 neo4j-init"
  exit 0
fi

etape() { echo "[lot4] $1"; t=$(date +%s); $CS -f "/lot4/init/$2"; echo "       $(( $(date +%s) - t )) s"; }

if [ "$FORCER" = "1" ]; then
  echo "[lot4] 0/4 Graphe vidé (les contraintes et les index restent)"
  $CS "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS"
fi
etape "1/4 Contraintes et index" 01-schema.cypher
etape "2/4 Catalogue : catégories, marques, gammes, produits, interfaces de compatibilité" 02-catalogue.cypher
etape "3/4 Clients, commandes et vues (export du Lot 3)" 03-achats.cypher
etape "4/4 Popularité et arêtes « achetés ensemble » (précalcul)" 04-precalculs.cypher

echo "[lot4] Graphe chargé :"
$CS --format plain "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS noeuds ORDER BY noeuds DESC" | sed 's/^/       /'
$CS --format plain "MATCH ()-[r]->() RETURN type(r) AS arete, count(*) AS nombre ORDER BY nombre DESC" | sed 's/^/       /'
echo "[lot4] OK en $(( $(date +%s) - debut )) s"
