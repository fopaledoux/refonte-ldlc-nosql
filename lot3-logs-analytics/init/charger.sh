#!/bin/sh
# =============================================================================
# Lot 3 — Chargement initial de Cassandra
# Exécuté par le conteneur ponctuel « cassandra-init » à chaque « docker compose up ».
# Ne recharge pas des données déjà présentes : les compteurs s'incrémentent, un
# second chargement doublerait les vues. Pour repartir de zéro : docker compose down -v
# Forcer le rechargement (keyspace supprimé puis recréé) :
#   docker compose run --rm -e FORCER=1 cassandra-init
# =============================================================================
set -e
H="${CASSANDRA_HOST:-127.0.0.1}"
debut=$(date +%s)

# Le job de classement s'exécute en dernier : s'il a écrit, le chargement est complet.
if [ "$FORCER" != "1" ] && cqlsh "$H" -e "SELECT rank FROM ldlc_analytics.rankings LIMIT 1" 2>/dev/null | grep -q "(1 rows)"; then
  echo "[lot3] Données déjà chargées (volume cassandra-data) : rien à faire."
  echo "       Pour repartir de zéro : docker compose down -v ; docker compose up -d"
  exit 0
fi

echo "[lot3] 1/3 Keyspace ldlc_analytics et tables"
# Un chargement interrompu laisse des compteurs partiels : on repart d'un keyspace vide.
cqlsh "$H" --request-timeout=120 -e "DROP KEYSPACE IF EXISTS ldlc_analytics"
cqlsh "$H" -f /lot3/init/01-schema.cql
cqlsh "$H" -e "DESCRIBE TABLES" -k ldlc_analytics | tr -s ' \n' ' ' | sed 's/^/       /'
echo

echo "[lot3] 2/3 Données de démonstration"
python3 /lot3/init/02-donnees-demo.py

echo "[lot3] 3/3 Classements précalculés"
python3 /lot3/jobs/classements.py

echo "[lot3] OK en $(( $(date +%s) - debut )) s"
