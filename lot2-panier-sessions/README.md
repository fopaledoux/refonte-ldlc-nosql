# Lot 2 — Panier et sessions utilisateurs (Redis)

**Statut : terminé et testé.** Conception et justifications : [`docs/conception.md`](../docs/conception.md#3-lot-2--panier-et-sessions-redis).

## En bref

| | |
|---|---|
| Technologie | Redis 7.2 (clé-valeur en mémoire) ; en production, Valkey 8 ou Redis 8 |
| Structures | Hash (sessions, paniers), Set et Hash (réplique du catalogue), Sorted set (sessions d'un client), Stream (événements) |
| Durées de vie | Session 30 min, panier invité 7 jours, panier client 30 jours, toutes **glissantes** |
| Opérations | 7 fonctions Lua atomiques appelées par `FCALL` ([`functions/ldlc.lua`](functions/ldlc.lua)) |
| Données | 16 222 SKU vendables et 95 limites d'achat exportés du Lot 1 ; 20 000 sessions et environ 13 300 paniers de démonstration |

## Lancer

Depuis la racine du dépôt (démarre aussi le Lot 1) :

```bash
docker compose up -d
```

Le conteneur `redis-init` charge les fonctions, la réplique du catalogue et les données de démonstration, puis s'arrête (environ 4 secondes) :

```bash
docker compose logs redis-init
```

## Exécuter les requêtes types

```bash
docker compose exec redis sh /lot2/queries/01-sessions.sh
```

```bash
docker compose exec redis sh /lot2/queries/02-panier.sh
```

```bash
docker compose exec redis sh /lot2/queries/03-mesures.sh
```

Chaque commande est affichée avant sa réponse. Les scripts sont rejouables.

> **Sous Git Bash (Windows)**, préfixer par `MSYS_NO_PATHCONV=1`. PowerShell, macOS et Linux ne sont pas concernés.

Pour explorer avec un client Redis (RedisInsight, `redis-cli`), se connecter à `localhost:6380`. Pour recharger la réplique du catalogue et les données de démonstration :

```bash
docker compose run --rm redis-init
```

## Contenu

| Fichier | Rôle |
|---|---|
| `config/redis.conf` | Persistance (AOF chaque seconde + RDB), plafond mémoire `volatile-lru`, notifications d'expiration, encodage listpack |
| `functions/ldlc.lua` | `cart_add`, `cart_set`, `cart_get`, `cart_merge`, `session_create`, `session_touch`, `session_login` |
| `init/charger.sh` | Chargement des fonctions, bascule atomique de la réplique du catalogue, données de démonstration |
| `init/donnees-demo.lua` | Génération déterministe de sessions et de paniers composés de vrais SKU |
| `data/skus-vendables.txt` | SKU en vente, exportés du Lot 1 |
| `data/limites-achat.txt` | Produits limités à 2 par client lors des sorties (RTX 5090/5080/5070 Ti, Ryzen 7 9800X3D) |
| `lib/commun.sh` | Paramètres métier (TTL, limite) et affichage |
| `queries/01-sessions.sh` | S1 à S8 : création, TTL glissant, connexion avec rotation d'identifiant, multi-appareils, déconnexions, expiration |
| `queries/02-panier.sh` | P1 à P9 : ajout, produit non vendable, limite de sortie, lecture, quantités, fusion, concurrence, événements, abandon |
| `queries/03-mesures.sh` | M1 à M4 : latence et débit, mémoire par clé, encodage, persistance |

## Questions du sujet et décisions

| Question | Décision |
|---|---|
| Session et panier : un objet ou deux ? | **Deux clés** : durées de vie différentes, le panier survit à la session |
| JSON ou Hash pour le panier ? | **Hash** : `HINCRBY` modifie une ligne sur place. Avec un JSON lu puis réécrit, 200 ajouts concurrents donnent 115 à 126 articles (P7) |
| TTL fixe ou glissant ? | **Glissant**, renouvelé par les fonctions à chaque action, lecture comprise |
| Panier invité à la connexion ? | **Fusion** atomique (`cart_merge`) : quantités additionnées dans la limite par produit, panier invité supprimé |
| Copier le prix dans le panier ? | **Non** : SKU et quantité seulement ; le prix vient du catalogue et fait foi à la commande |
| Redis tombe ? | AOF chaque seconde (perte maximale environ 1 s), réplicas + Sentinel ; disponibilité privilégiée (AP) |

## Résultats mesurés (portable, Docker Desktop, sans réseau)

| Mesure | Résultat |
|---|---|
| Latence `session_touch` / `cart_add`, 1 client | p50 0,14 / 0,18 ms ; p95 0,32 / 0,45 ms |
| Débit `cart_add`, 50 clients | environ 11 500 appels/s |
| Mémoire | session 225 octets, panier environ 130 octets ; pic estimé à environ 120 Mo |
| Concurrence (P7) | Hash : 200/200 ; JSON lu puis réécrit : 115 à 126/200 |
