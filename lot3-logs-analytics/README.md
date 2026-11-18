# Lot 3 — Logs et analytics (Cassandra)

**Statut : terminé et testé.** Conception et justifications : [`docs/conception.md`](../docs/conception.md#4-lot-3--logs-et-analytics-cassandra).

## En bref

| | |
|---|---|
| Technologie | Cassandra 5.0 (wide-column, sans maître) ; un nœud en développement, 3 nœuds et un facteur de réplication de 3 en production |
| Modèle | 8 tables pour 9 requêtes (Query-Driven), un type `order_line` |
| Partitions | Bucketing des événements (16 par heure), sharding des compteurs du jour (16), aucune partition au-delà de ~11 Mo au pic |
| Rétention | Événements bruts 90 jours (TTL, compaction par fenêtre d'un jour), agrégats et classements 25 mois, commandes 10 ans |
| Cohérence | `LOCAL_ONE` pour les événements et compteurs, `LOCAL_QUORUM` pour les commandes et les prix |
| Données | 7 jours d'événements (environ 97 000), 28 jours de compteurs, 12 mois de commandes (environ 23 000, composées comme de vrais paniers) et de prix (77 645), sur les 16 222 produits en vente du Lot 1 |

## Lancer

Depuis la racine du dépôt (démarre aussi les Lots 1 et 2) :

```bash
docker compose up -d
```

Cassandra démarre en environ 1 minute. Le conteneur `cassandra-init` crée ensuite le keyspace, charge les données de démonstration et lance le job de classement, puis s'arrête (environ 2 min 30) :

```bash
docker compose logs -f cassandra-init
```

Les données sont datées par rapport au jour du chargement : « aujourd'hui » est le jour du `docker compose up`. Le chargement ne se rejoue pas sur des données existantes (les compteurs seraient doublés). Pour recharger le Lot 3 seul (keyspace supprimé, puis recréé) :

```bash
docker compose run --rm -e FORCER=1 cassandra-init
```

Pour repartir de zéro (toutes les bases) :

```bash
docker compose down -v
```

## Exécuter les requêtes types

```bash
docker compose exec cassandra python3 /lot3/queries/01-lectures.py
```

```bash
docker compose exec cassandra python3 /lot3/queries/02-ecritures.py
```

```bash
docker compose exec cassandra sh /lot3/queries/03-mesures.sh
```

Chaque requête CQL est affichée avant son résultat, avec la trace serveur pour les lectures : partitions lues, SSTables, lignes et tombstones. Les deux premiers scripts sont rejouables. Le troisième dure environ 4 minutes.

D'autres commandes utiles :

```bash
docker compose exec cassandra python3 /lot3/jobs/classements.py
```

```bash
docker compose exec cassandra python3 /lot3/connecteurs/panier.py
```

```bash
docker compose exec cassandra cqlsh -k ldlc_analytics
```

L'export vers le Lot 4 (commandes et vues des clients connectés) régénère `lot4-recommandations/data/commandes.tsv` et `vues.tsv`, et indique pour chacun s'il est inchangé ou mis à jour :

```bash
docker compose run --rm cassandra-init python3 /lot3/jobs/export_graphe.py
```

> **Sous Git Bash (Windows)**, préfixer par `MSYS_NO_PATHCONV=1`. PowerShell, macOS et Linux ne sont pas concernés.

Pour un client graphique (DBeaver, DataStax Studio), se connecter à `localhost:9043`, datacenter `dc1`.

## Contenu

| Fichier | Rôle |
|---|---|
| `init/01-schema.cql` | Keyspace et 8 tables, chacune commentée : requête servie, taille de partition, TTL, compaction |
| `init/02-donnees-demo.py` | Génération déterministe : visites, recherches, sortie de la RTX 5090 à J-2, historique des prix, commandes composées comme de vrais paniers (montages aux pièces compatibles, évolutions, périphériques, achats isolés) |
| `init/charger.sh` | Chargement initial (conteneur `cassandra-init`), ou rechargement avec `FORCER=1` |
| `lib/commun.py` | Connexion et profils de cohérence, formules de bucket et de shard, écriture regroupée par partition, affichage et traces |
| `jobs/classements.py` | Top des produits (global, par catégorie) et des recherches, par jour, par semaine et sur 7 jours glissants |
| `jobs/export_graphe.py` | Export vers le Lot 4 : une ligne par article commandé, et les vues des clients connectés sur 7 jours |
| `connecteurs/panier.py` | Stream Redis `events:cart` (Lot 2) vers Cassandra : groupe de consommateurs, écriture puis `XACK` |
| `queries/01-lectures.py` | Q1 à Q9, puis les requêtes que Cassandra refuse (`ALLOW FILTERING`, `ORDER BY`, `GROUP BY`) |
| `queries/02-ecritures.py` | W1 à W6 : chemin d'un clic, rejeu (idempotence), `null` ou `UNSET`, commande en BATCH journalisé, prix de référence Omnibus, connecteur du panier |
| `queries/03-mesures.sh` | M1 à M4 : `cassandra-stress` (latence, débit, lecture pendant l'écriture), disque et partitions, projection au volume réel |
| `stress/evenements.yaml` | Profil `cassandra-stress` : la table `events_by_hour` |
| `data/produits.tsv` | Produits en vente exportés du Lot 1 : SKU, catégorie, marque, prix, nombre d'avis, nom, caractéristiques, attributs de compatibilité (socket, mémoire, formats, puce) |

Les événements, les commandes et l'historique des prix sont **synthétiques** (graine fixe 20261117) ; les produits, leurs noms et leurs prix courants sont ceux du Lot 1. Pour régénérer cet export (et ceux du Lot 2) après une modification du catalogue, automatiquement depuis une copie du catalogue à l'état initial (Git Bash : préfixer par `MSYS_NO_PATHCONV=1`) :

```bash
docker compose run --rm mongo-init python /lot1/lib/exports.py
```

## Questions du sujet et décisions

| Question | Décision |
|---|---|
| Combien de tables ? | **8 pour 9 requêtes**, une par requête : chaque clic est écrit 2 fois et compté 2 fois |
| Clé de partition des événements ? | **`(day, hour, bucket)`** : sans bucket, une heure de pointe ferait une partition de 176 Mo sur 3 réplicas. Avec 16 buckets : 11 Mo |
| Sortie d'une carte graphique ? | Événements répartis par bucket. Compteurs **agrégés par lot** avant incrément, ce qui donne une écriture toutes les quelques secondes |
| Vues du jour : compter à la lecture ou à l'écriture ? | **À l'écriture** (`COUNTER`) : Q1 lit une ligne. Un compteur n'est pas idempotent et n'accepte pas de TTL, ce qui est accepté et documenté (W2) |
| Top de la semaine ? | **Précalculé par un job horaire** à partir de 112 partitions connues, lu en une partition déjà triée. CQL refuse le tri sur un agrégat |
| TTL ? | Événements bruts 90 jours, comptés à partir de la date de l'événement. Agrégats et classements 25 mois (CNIL). Commandes 10 ans |
| Cohérence ? | `LOCAL_ONE` pour les événements, `LOCAL_QUORUM` pour les commandes et les prix (2 + 2 > 3) |
| Source de vérité des commandes ? | **Non** : le système de commandes (ACID). Cassandra garde l'historique, en lecture |

## Résultats mesurés (portable, un nœud Docker, mémoire saturée)

| Mesure | Résultat |
|---|---|
| Lecture d'une partition, Q1 à Q8 | 3 à 10 ms (trace serveur), 0 tombstone |
| Écriture d'un événement, 4 clients | p50 2 à 3,5 ms, p95 12 à 34 ms selon les exécutions |
| Débit d'écriture, 32 clients | 1 750 à 2 000 événements/s ; le besoin au pic d'une sortie est d'environ 420 événements (840 écritures) par seconde |
| Disque | 121 octets par événement (2 tables, compressés) ; 0,16 To pour 90 jours à 5 millions par jour, 3 réplicas |
| Équilibre des buckets à l'heure de la sortie | de 57 à 83 lignes par partition |
