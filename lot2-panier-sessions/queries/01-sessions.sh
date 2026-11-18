#!/bin/sh
# =============================================================================
# Lot 2 — Requêtes types : SESSIONS
# Lancement : docker compose exec redis sh /lot2/queries/01-sessions.sh
# Rejouable : les clés de démonstration sont recréées à chaque exécution.
# =============================================================================
. /lot2/lib/commun.sh
IDX=customer:CUST-000042:sessions
nettoyer session:demo-invite session:demo-connecte session:demo-mobile-invite session:demo-mobile session:demo-courte $IDX

titre "S1 - Première visite : création d'une session anonyme" \
  "Un Hash avec un TTL de 30 min. Créée par un serveur web, lisible par tous les autres."
r FCALL session_create 1 session:demo-invite $TTL_SESSION locale fr-FR device desktop cartKey cart:guest:demo-g1
r TTL session:demo-invite

titre "S2 - À chaque requête HTTP : lecture de la session et TTL glissant" \
  "On simule 25 minutes d'inactivité (TTL ramené à 300 s), puis le visiteur revient."
rq EXPIRE session:demo-invite 300 > /dev/null
r TTL session:demo-invite
r FCALL session_touch 1 session:demo-invite $TTL_SESSION
r TTL session:demo-invite
note "Le TTL repart à 1800 s : la session expire après 30 minutes d'INACTIVITÉ."
note "Session expirée ou inconnue : la fonction renvoie nil, le serveur en crée une nouvelle."
r FCALL session_touch 1 session:inconnue $TTL_SESSION

titre "S3 - Connexion : rattachement du client ET changement d'identifiant de session" \
  "RENAME atomique : protège contre la fixation de session."
r FCALL session_login 3 session:demo-invite session:demo-connecte $IDX CUST-000042 demo-connecte cart:customer:CUST-000042 $TTL_SESSION $TTL_CLIENT
r EXISTS session:demo-invite
note "L'identifiant d'avant la connexion n'existe plus : volé ou imposé, il est inutilisable."

titre "S4 - Le même client se connecte aussi sur son mobile" \
  "L'index customer:{id}:sessions liste ses sessions (score = date de connexion)."
rq FCALL session_create 1 session:demo-mobile-invite $TTL_SESSION locale fr-FR device mobile > /dev/null
rq FCALL session_login 3 session:demo-mobile-invite session:demo-mobile $IDX CUST-000042 demo-mobile cart:customer:CUST-000042 $TTL_SESSION $TTL_CLIENT > /dev/null
r ZRANGE $IDX 0 -1 WITHSCORES

titre "S5 - Déconnexion du mobile : deux écritures dans une transaction (MULTI / EXEC)"
rtx "DEL session:demo-mobile" "ZREM $IDX demo-mobile"
r ZRANGE $IDX 0 -1

titre "S6 - « Me déconnecter de tous mes appareils »"
cles=""
for id in $(rq ZRANGE $IDX 0 -1); do cles="$cles session:$id"; done
r DEL $cles $IDX
note "Une session dont l'identifiant figure encore dans l'index mais qui a expiré est ignorée (DEL ne fait rien)."

titre "S7 - Expiration automatique : aucune purge à programmer" \
  "Session créée avec un TTL de 2 s pour la démonstration."
r FCALL session_create 1 session:demo-courte 2 locale fr-FR
r TTL session:demo-courte
# On interroge Redis chaque seconde plutôt que d'attendre une durée fixe : sous
# Docker Desktop (Windows, macOS), l'horloge de la machine virtuelle peut dériver.
attente=0
while [ "$(rq EXISTS session:demo-courte)" = "1" ] && [ $attente -lt 15 ]; do sleep 1; attente=$((attente + 1)); done
echo "   (clé disparue après ${attente} s d'attente)"
r EXISTS session:demo-courte

titre "S8 - Combien de sessions actives en ce moment ? (données de démonstration)" \
  "SCAN parcourt les clés par petits lots sans bloquer Redis ; KEYS * est proscrit en production."
echo "   $(rq --scan --pattern 'session:*' --count 1000 | wc -l) sessions actives"
echo "   $(rq --scan --pattern 'customer:*:sessions' --count 1000 | wc -l) clients connectés sur au moins un appareil"
