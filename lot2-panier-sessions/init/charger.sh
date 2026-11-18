#!/bin/sh
# =============================================================================
# Lot 2 — Chargement initial de Redis
# Exécuté par le conteneur ponctuel « redis-init » à chaque « docker compose up ».
# Idempotent : peut être relancé sans effet de bord.
#   docker compose run --rm redis-init
# =============================================================================
set -e
H="${REDIS_HOST:-127.0.0.1}"
R="redis-cli -h $H"
debut=$(date +%s)

echo "[lot2] 1/3 Fonctions Lua (bibliothèque ldlc)"
$R -x FUNCTION LOAD REPLACE < /lot2/functions/ldlc.lua

echo "[lot2] 2/3 Réplique du catalogue : SKU vendables et limites d'achat"
# Chargées dans des clés temporaires, puis basculées par RENAME (atomique) :
# un ajout au panier ne voit jamais un catalogue vide ou à moitié chargé.
# En production, la même bascule est faite par le job qui suit les changements du Lot 1.
$R DEL catalog:sellable:next catalog:limits:next > /dev/null
xargs -n 2000 $R SADD catalog:sellable:next < /lot2/data/skus-vendables.txt > /dev/null
grep -v '^#' /lot2/data/limites-achat.txt | xargs -n 200 $R HSET catalog:limits:next > /dev/null
$R RENAME catalog:sellable:next catalog:sellable > /dev/null
$R RENAME catalog:limits:next catalog:limits > /dev/null
echo "       $($R SCARD catalog:sellable) SKU vendables, $($R HLEN catalog:limits) produits à quantité limitée"

echo "[lot2] 3/3 Données de démonstration : sessions et paniers"
$R --eval /lot2/init/donnees-demo.lua , 20000 8000 6000 | tr '\n' ' ' | \
  awk '{ printf "       %s sessions, %s paniers invités, %s paniers clients\n", $1, $2, $3 }'

echo "[lot2] OK : $($R DBSIZE) clés en $(( $(date +%s) - debut )) s"
