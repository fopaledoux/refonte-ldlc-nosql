#!/bin/sh
# =============================================================================
# Lot 2 — Requêtes types : PANIER
# Lancement : docker compose exec redis sh /lot2/queries/02-panier.sh
# Rejouable : les clés de démonstration sont recréées à chaque exécution.
# =============================================================================
. /lot2/lib/commun.sh
G=cart:guest:demo-g1                 # panier du visiteur non connecté
C=cart:customer:CUST-000042          # panier du même visiteur une fois connecté
CAT="catalog:sellable catalog:limits events:cart"
nettoyer $G $C cart:demo-concurrence cart:demo-json cart:demo-abandon

titre "P1 - Ajout au panier d'un visiteur non connecté" \
  "cart_add : produit vendable ? limite ? puis ajout, TTL prolongé et événement publié, en une opération atomique."
r FCALL cart_add 4 $G $CAT MB-000131 1 $TTL_INVITE $LIMITE
r FCALL cart_add 4 $G $CAT CPU-000248 1 $TTL_INVITE $LIMITE
r FCALL cart_add 4 $G $CAT MB-000131 1 $TTL_INVITE $LIMITE
note "Réponse : quantité dans le panier, limite appliquée, quantité réellement ajoutée."

titre "P2 - Produit qui n'est plus vendu (CPU-000025, arrêté dans le Lot 1)"
r FCALL cart_add 4 $G $CAT CPU-000025 1 $TTL_INVITE $LIMITE

titre "P3 - Jour de sortie : RTX 5090 limitée à 2 par client" \
  "La limite vient de catalog:limits, alimenté par le catalogue."
r FCALL cart_add 4 $G $CAT GPU-000009 5 $TTL_INVITE $LIMITE
r FCALL cart_add 4 $G $CAT GPU-000009 1 $TTL_INVITE $LIMITE
note "5 demandées, 2 accordées ; un nouvel essai n'ajoute rien."

titre "P4 - Affichage du panier : lecture + TTL glissant"
r FCALL cart_get 1 $G $TTL_INVITE
r TTL $G
r OBJECT ENCODING $G
r MEMORY USAGE $G
note "Le panier ne contient que des SKU et des quantités (encodage compact listpack, quelques dizaines d'octets)."
note "Nom, image et prix viennent du catalogue (Lot 1). Le prix n'est copié nulle part : il fait foi à la commande."

titre "P5 - Modifier une quantité, puis supprimer la ligne"
r FCALL cart_set 3 $G catalog:limits events:cart CPU-000248 3 $TTL_INVITE $LIMITE
r FCALL cart_set 3 $G catalog:limits events:cart CPU-000248 0 $TTL_INVITE $LIMITE
r HGETALL $G
r FCALL cart_set 3 $G catalog:limits events:cart CPU-000248 2 $TTL_INVITE $LIMITE

titre "P6 - Connexion : fusion du panier invité dans le panier client" \
  "Le client avait déjà mis la carte mère MB-000131 dans son panier, depuis un autre appareil."
rq HSET $C MB-000131 1 > /dev/null
rq EXPIRE $C $TTL_CLIENT > /dev/null
r HGETALL $C
r FCALL cart_merge 4 $G $C catalog:limits events:cart $TTL_CLIENT $LIMITE
r HGETALL $C
r EXISTS $G
note "Quantités additionnées (1 + 2 = 3 cartes mères), limite de la RTX 5090 respectée, panier invité supprimé."

titre "P7 - Pourquoi un Hash avec des opérations atomiques ?" \
  "Deux serveurs web ajoutent chacun 100 fois le même article, en même temps."
for serveur in 1 2; do
  ( i=0; while [ $i -lt 100 ]; do rq HINCRBY cart:demo-concurrence PASTE-000051 1 > /dev/null; i=$((i + 1)); done ) &
done
wait
echo "   Hash + HINCRBY (modification sur place)       : $(rq HGET cart:demo-concurrence PASTE-000051) articles"
rq SET cart:demo-json '{"qty":0}' > /dev/null
for serveur in 1 2; do
  ( i=0; while [ $i -lt 100 ]; do
      v=$(rq GET cart:demo-json | tr -cd '0-9')
      rq SET cart:demo-json "{\"qty\":$((v + 1))}" > /dev/null
      i=$((i + 1))
    done ) &
done
wait
echo "   Bloc JSON lu, modifié puis réécrit (GET/SET)  : $(rq GET cart:demo-json | tr -cd '0-9') articles"
note "Attendu : 200. Avec GET puis SET, deux serveurs lisent la même valeur et l'un écrase l'autre."
nettoyer cart:demo-concurrence cart:demo-json

titre "P8 - Événements du panier (outbox) lus par le Lot 3" \
  "Chaque fonction du panier écrit son événement dans le Stream events:cart, dans le même appel atomique."
r XLEN events:cart
r XREVRANGE events:cart + - COUNT 3
rq XGROUP DESTROY events:cart lot3 > /dev/null
rq XGROUP CREATE events:cart lot3 0 > /dev/null
r XREADGROUP GROUP lot3 connecteur-1 COUNT 2 STREAMS events:cart ">"
premier=$(rq XPENDING events:cart lot3 - + 1 | head -n 1)
r XACK events:cart lot3 "$premier"
r XPENDING events:cart lot3
note "Le connecteur du Lot 3 lit à son rythme et acquitte ; un événement non acquitté sera relu après une panne."

titre "P9 - Panier abandonné : son expiration publie un événement" \
  "Canal __keyevent@0__:expired (réglage notify-keyspace-events Ex). Panier créé avec un TTL de 1 s."
timeout 20 redis-cli -h "$H" SUBSCRIBE __keyevent@0__:expired > /tmp/expirations.txt &
abonne=$!
sleep 1
rq HSET cart:demo-abandon MB-000131 1 > /dev/null
r EXPIRE cart:demo-abandon 1
# Une clé expirée devient invisible immédiatement, mais Redis ne la supprime (et ne
# publie l'événement) que lorsqu'on y accède ou que son échantillonnage en arrière-plan
# la trouve. Avec des dizaines de milliers de clés à TTL, cela peut prendre des minutes :
# la boucle lit donc la clé (EXISTS) pour déclencher la suppression.
attente=0
while ! grep -q "cart:demo-abandon" /tmp/expirations.txt && [ $attente -lt 15 ]; do
  rq EXISTS cart:demo-abandon > /dev/null
  sleep 1; attente=$((attente + 1))
done
kill $abonne 2> /dev/null; wait $abonne 2> /dev/null
if grep -q "cart:demo-abandon" /tmp/expirations.txt; then
  echo "   Message reçu par l'abonné après ${attente} s : canal __keyevent@0__:expired, clé cart:demo-abandon"
else
  echo "   Aucun message reçu en 15 s."
fi
note "Cet événement peut arriver en retard et ne contient plus le panier (supprimé avec la clé) :"
note "c'est un simple signal. La source fiable pour le Lot 3 reste le flux events:cart (P8)."
