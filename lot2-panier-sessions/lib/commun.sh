# =============================================================================
# Lot 2 — Utilitaires partagés par les scripts de requêtes types (sh / BusyBox)
# =============================================================================
H="${REDIS_HOST:-127.0.0.1}"

# Paramètres métier (secondes) : ce sont les valeurs passées aux fonctions Lua
TTL_SESSION=1800      # 30 minutes d'inactivité
TTL_INVITE=604800     # 7 jours pour le panier d'un visiteur non connecté
TTL_CLIENT=2592000    # 30 jours pour le panier d'un client connecté
LIMITE=10             # quantité maximale par produit, hors produits limités

# Affiche la commande, puis la réponse formatée comme dans redis-cli interactif
r() { echo "> $*"; redis-cli -h "$H" --no-raw "$@"; }
# Exécute sans rien afficher d'autre que la réponse brute
rq() { redis-cli -h "$H" "$@"; }
# Transaction : toutes les commandes passent dans une seule connexion, entre MULTI et EXEC
rtx() {
  echo "> MULTI"; for c in "$@"; do echo ">   $c"; done; echo "> EXEC"
  { echo MULTI; for c in "$@"; do echo "$c"; done; echo EXEC; } | redis-cli -h "$H" --no-raw | tail -n "$#"
}
titre() {
  echo
  echo "=============================================================================="
  echo "$1"
  [ -n "$2" ] && echo "   $2"
  echo "=============================================================================="
}
note() { echo "   -> $*"; }
nettoyer() { rq DEL "$@" > /dev/null; }
