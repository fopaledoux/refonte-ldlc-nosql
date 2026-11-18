# =============================================================================
# Lot 4 — Utilitaires partagés par les scripts de requêtes types (sh, cypher-shell)
# =============================================================================
H="${NEO4J_HOST:-localhost}"
# cypher-shell est un programme Java : compilation rapide seulement, il ne vit que quelques secondes
export JAVA_OPTS="-XX:TieredStopAtLevel=1 -XX:+UseSerialGC"
CS="cypher-shell -a neo4j://$H:7687 --non-interactive"

# q 'paramètres' 'requête' : affiche la requête (et ses paramètres), puis le résultat
q() {
  if [ -n "$1" ]; then echo ":param $1"; fi
  printf 'neo4j> %s\n' "$(echo "$2" | tr -s ' \n' ' ')"
  if [ -n "$1" ]; then $CS --format verbose -P "$1" "$2"; else $CS --format verbose "$2"; fi | grep -v "^ready to start"
}

# profil 'paramètres' 'requête' : exécute deux fois (cache chaud) et n'affiche que le bilan du PROFILE
profil() {
  if [ -n "$1" ]; then set -- "-P" "$1" "PROFILE $2"; else set -- "PROFILE $2"; fi
  $CS --format verbose "$@" > /dev/null
  $CS --format verbose "$@" | grep -E "^(Total database accesses|ready to start)" | \
    sed -E 's/ready to start consuming query after ([0-9]+) ms, results consumed after another ([0-9]+) ms/temps : \1 + \2 ms/' | \
    tr '\n' ' ' | sed 's/^/   [profil] /'
  echo
}

titre() {
  echo
  echo "=============================================================================="
  echo "$1"
  [ -n "$2" ] && echo "   $2"
  echo "=============================================================================="
}
note() { echo "   -> $*"; }
