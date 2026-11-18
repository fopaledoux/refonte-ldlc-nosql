#!/bin/sh
# =============================================================================
# Lot 2 — Mesures : latence, mémoire, encodage, persistance
# Lancement : docker compose exec redis sh /lot2/queries/03-mesures.sh
# =============================================================================
. /lot2/lib/commun.sh

titre "M1 - Latence et débit (redis-benchmark)" \
  "Mesuré dans le conteneur, sans réseau : en production, ajouter ~0,1 à 0,3 ms de réseau dans un même datacenter."
# bench <libellé> <clients> <appels> <commande…> : une ligne p50 / p95 / p99 / débit
bench() {
  lib=$1; clients=$2; appels=$3; shift 3
  redis-benchmark -h "$H" -n "$appels" -c "$clients" --csv "$@" | tail -n 1 | tr -d '"' |
    awk -F, -v lib="$lib" '{ printf "   %-18s p50 %5.2f ms | p95 %5.2f ms | p99 %5.2f ms | %6.0f appels/s\n", lib, $5, $6, $7, $2 }'
}
nettoyer session:bench cart:bench events:bench
rq FCALL session_create 1 session:bench $TTL_SESSION locale fr-FR > /dev/null
rq HSET cart:bench MB-000131 1 CPU-000248 1 PASTE-000051 2 > /dev/null
echo "   Un seul client (latence d'un appel, sans file d'attente), 20 000 appels :"
bench "PING seul" 1 20000 PING
bench "session_touch" 1 20000 FCALL session_touch 1 session:bench $TTL_SESSION
bench "cart_get" 1 20000 FCALL cart_get 1 cart:bench $TTL_INVITE
bench "cart_add" 1 20000 FCALL cart_add 4 cart:bench catalog:sellable catalog:limits events:bench PASTE-000051 1 $TTL_INVITE 1000000
echo
echo "   50 clients simultanés (débit maximal), 100 000 appels :"
bench "cart_add" 50 100000 FCALL cart_add 4 cart:bench catalog:sellable catalog:limits events:bench PASTE-000051 1 $TTL_INVITE 1000000
nettoyer session:bench cart:bench events:bench
note "p50 = médiane, p99 = 99 % des appels répondent plus vite. À 50 clients, la latence mesure surtout"
note "l'attente dans la file (50 clients / débit) : c'est le signal pour ajouter de la capacité, pas le coût d'un appel."

titre "M2 - Mémoire : coût moyen de chaque type de clé" \
  "Moyenne de MEMORY USAGE sur 500 clés de chaque type (données de démonstration)."
moyenne() {
  rq --scan --pattern "$1" --count 1000 | head -n 500 | while read -r cle; do rq MEMORY USAGE "$cle"; done |
    awk '{ s += $1 } END { if (NR) printf "%d", s / NR; else printf "0" }'
}
SESSION=$(moyenne 'session:*')
INVITE=$(moyenne 'cart:guest:*')
CLIENT=$(moyenne 'cart:customer:*')
echo "   session           : $SESSION octets"
echo "   panier invité     : $INVITE octets"
echo "   panier client     : $CLIENT octets"
echo
echo "   Projection (hypothèses de dimensionnement, à ajuster avec les chiffres réels de LDLC) :"
awk -v s="$SESSION" -v i="$INVITE" -v c="$CLIENT" 'BEGIN {
  ns = 150000; ni = 300000; nc = 400000
  printf "     %7d sessions simultanées (pic d une sortie de composant) : %6.1f Mo\n", ns, ns * s / 1048576
  printf "     %7d paniers invités (7 jours de rétention)               : %6.1f Mo\n", ni, ni * i / 1048576
  printf "     %7d paniers clients (30 jours de rétention)              : %6.1f Mo\n", nc, nc * c / 1048576
  printf "     Total : %.0f Mo, soit quelques pour cent de la RAM d un serveur.\n", (ns * s + ni * i + nc * c) / 1048576
}'

titre "M3 - Encodage compact des petits paniers (listpack)" \
  "Un panier de 3 lignes, puis un panier de 130 lignes (au-delà du seuil de 128)."
nettoyer cart:demo-petit cart:demo-gros
rq HSET cart:demo-petit MB-000131 1 CPU-000248 1 PASTE-000051 2 > /dev/null
rq SRANDMEMBER catalog:sellable 130 | while read -r sku; do echo "HSET cart:demo-gros $sku 1"; done | redis-cli -h "$H" > /dev/null
for cle in cart:demo-petit cart:demo-gros; do
  echo "   $cle : $(rq HLEN $cle) lignes, encodage $(rq OBJECT ENCODING $cle), $(rq MEMORY USAGE $cle) octets"
done
nettoyer cart:demo-petit cart:demo-gros
note "En listpack, le panier est stocké d'un seul bloc contigu : peu de mémoire, lecture rapide."

titre "M4 - Persistance et politique mémoire en vigueur"
r CONFIG GET appendonly
r CONFIG GET appendfsync
r CONFIG GET maxmemory-policy
r CONFIG GET notify-keyspace-events
rq INFO persistence | grep -E "^(aof_enabled|aof_last_write_status|rdb_last_bgsave_status)" | tr -d '\r' | sed 's/^/   /'
rq INFO memory | grep -E "^(used_memory_human|maxmemory_human)" | tr -d '\r' | sed 's/^/   /'
rq INFO keyspace | grep '^db0' | tr -d '\r' | sed 's/^/   /'
note "expires = clés qui ont un TTL : toutes, sauf la réplique du catalogue et le flux d'événements."
