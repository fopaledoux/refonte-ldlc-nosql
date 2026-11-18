# Conventions communes aux quatre lots

Ces règles garantissent que les lots s'assemblent. Toute exception se discute en binôme et s'écrit ici.

## Identifiant produit : le SKU

- Format : `PREFIXE-000000` (lettres majuscules, tiret, 6 chiffres), contrôlé par le validateur MongoDB.
- C'est le `_id` des produits dans MongoDB. Les autres lots le **réutilisent tel quel** : clé de panier (Lot 2), colonne des événements (Lot 3), propriété `sku` des nœuds `Product` (Lot 4).
- Préfixes : `CPU` processeurs · `MB` cartes mères · `RAM` mémoire · `GPU` cartes graphiques · `CASE` boîtiers · `PSU` alimentations · `COOL` ventirads · `FAN` ventilateurs · `FANC` contrôleurs · `PASTE` pâtes thermiques · `SND` cartes son · `ODD` lecteurs optiques · `ACC` accessoires · `DRV` stockage interne · `EXT` stockage externe · `MON` écrans · `KBD` claviers · `MOUSE` souris · `HEAD` casques · `SPK` enceintes · `CAM` webcams · `NET` cartes réseau · `WIFI` cartes Wi-Fi · `UPS` onduleurs · `SOFT` logiciels · `VR` casques VR (ajouté en V1).
- Liste des SKU en vente, pour générer les données des autres lots :

```bash
docker compose run --rm -T mongo-init python /lot1/lib/exports.py skus
```

## Autres identifiants et formats

| Donnée | Convention |
|---|---|
| Client | `CUST-000000` (avis du Lot 1, paniers et sessions du Lot 2) |
| Clés Redis (Lot 2) | `type:sous-type:identifiant`, en anglais, séparateur `:` (ex. `cart:customer:CUST-000042`, `session:{id}`). Toute clé de panier ou de session a un TTL. |
| Événements | Paires champ/valeur avec `ts` (millisecondes UTC), `type` et le SKU tel quel (ex. Stream `events:cart`, lu par le Lot 3) |
| Commande | `ORD-00000000` (8 chiffres) |
| Tables Cassandra (Lot 3) | Keyspace `ldlc_analytics` ; une table par requête, nommée `<contenu>_by_<clé de partition>` (ex. `orders_by_customer`) |
| Identifiant d'événement | `event_id` (UUID) fixé par le collecteur, jamais par la base : un événement rejoué réécrit la même ligne. Pour un événement Redis : UUID v5 de l'identifiant d'entrée du Stream |
| Identifiant de visite | `visit_id` (UUID), distinct du jeton de session du Lot 2 : un jeton est un secret et n'est jamais journalisé |
| Répartition (Lot 3) | `bucket = event_id mod 16`, `shard = CRC32(SKU) mod 16` : les mêmes formules dans l'ingestion, les jobs et l'API ([`lib/commun.py`](../lot3-logs-analytics/lib/commun.py)) |
| Graphe Neo4j (Lot 4) | Labels au singulier, en anglais, en `PascalCase` (`Product`, `Customer`, `Order`) ; un second label par type de composant (`CPU`, `Motherboard`, `Memory`, `Case`, `GPU`) ; relations en majuscules, un verbe (`BOUGHT_WITH`, `REQUIRES`, `PROVIDES`) ; propriétés en `snake_case` |
| Interfaces de compatibilité (Lot 4) | Identifiant `type:valeur` : `socket:AM5`, `memory:DDR5`, `form:Mini ITX`. Un produit `REQUIRES` une interface qu'un autre `PROVIDES` |
| Montants | Decimal (`Decimal128` dans MongoDB, `decimal` en CQL), jamais de flottant ; devise explicite (`USD` pour les données importées). Neo4j n'a pas de type décimal : centimes entiers (`price_cents`) |
| Dates | UTC, ISO 8601 ; les jours des tables du Lot 3 (`day`) sont des jours UTC |
| Noms techniques | Collections, tables, clés, labels et champs en anglais (`snake_case` pour les attributs, `camelCase` pour les champs MongoDB) ; commentaires et documentation en français |

## Échanges entre les lots

- **Une base ne lit jamais celle d'un autre lot pendant une requête du site.** Elle travaille sur sa propre copie, adaptée à ses requêtes, et reçoit les changements par un flux : Change Streams de MongoDB, Stream Redis `events:cart`, bus d'événements.
- **Chaque copie a une source de vérité unique,** nommée dans [`architecture.md`](architecture.md) (« Où vit chaque donnée »).
- **En démonstration, les flux sont remplacés par des exports versionnés dans le `data/` du lot qui les reçoit.** Chaque lot se lance donc seul, sans les bases des autres.
  - Du Lot 1, par `lot1-catalogue/lib/exports.py`, en une commande : `skus-vendables.txt` et `limites-achat.txt` (Lot 2), `produits.tsv` avec les attributs de compatibilité (Lots 3 et 4), `categories.tsv` (Lot 4). L'export part d'une copie temporaire du catalogue à l'état initial : il ne dépend pas des écritures de démonstration en cours.
  - Du Lot 3, par `lot3-logs-analytics/jobs/export_graphe.py`, en une commande : `commandes.tsv` et `vues.tsv` (Lot 4).
  - Du Lot 4 vers le Lot 1, par un job et non par un fichier : `lot4-recommandations/jobs/compatibles_vers_lot1.py` réécrit la liste courte `compatibleProducts` des fiches.

  Les commandes sont dans le README du lot qui exporte.

## Ports et accès (développement)

| Base | Lot | Port sur la machine hôte | Client |
|---|---|---|---|
| MongoDB 8 | 1 | `27018` | `mongodb://localhost:27018` (Compass, mongosh) |
| Redis 7.2 | 2 | `6380` | `redis-cli -p 6380`, RedisInsight |
| Cassandra 5.0 | 3 | `9043` | `cqlsh localhost 9043`, DBeaver (datacenter `dc1`) |
| Neo4j 5.26 | 4 | `7475` (Browser et API HTTP), `7688` (Bolt) | Neo4j Browser `http://localhost:7475`, connexion `bolt://localhost:7688` |
| Prototype d'API | bonus | `8000` | `http://localhost:8000/docs` (Swagger) |

Les ports diffèrent des ports standard pour ne pas gêner une base déjà installée sur le poste. Aucune base n'a d'authentification : c'est réservé au développement.

## Organisation du dépôt

Modèle commun à chaque lot :

```
lotN-<nom>/
├── README.md        choix, modèle, questions tranchées, mode d'emploi
├── init/            scripts exécutés au premier démarrage du conteneur
├── lib/             code partagé entre scripts
├── queries/         requêtes types exécutables, numérotées
└── data/            données (source documentée dans un SOURCE.md)
```

Le dépôt complet :

```
.
├── README.md                 membres, présentation, démarrage, livrables
├── docker-compose.yml        un service par base, plus un conteneur ponctuel de chargement par lot
├── docs/                     conception.md, architecture.md, conventions.md
├── lot1-catalogue/           MongoDB : Dockerfile (Python + pymongo), init/ (schéma, import, avis,
│                             compatibilités, index), lib/ (dont exports.py), queries/, data/raw/
├── lot2-panier-sessions/     Redis : config/redis.conf, functions/ldlc.lua, init/, lib/, queries/, data/
├── lot3-logs-analytics/      Cassandra : init/ (schéma CQL, données), lib/, jobs/ (classements),
│                             connecteurs/ (panier Redis), queries/, stress/ (cassandra-stress), data/
├── lot4-recommandations/     Neo4j : init/ (Cypher : contraintes, catalogue, achats, précalculs), lib/,
│                             queries/ (recommandations, mesures), jobs/ (compatibles vers le Lot 1), data/
└── api/                      prototype d'API (FastAPI) : routes en anglais, une par requête type des lots
```

Chaque nouvelle base s'ajoute comme un service de `docker-compose.yml`, avec ses scripts d'initialisation montés en lecture seule, comme pour `mongodb`. Chaque base reçoit un conteneur ponctuel `<base>-init` (`mongo-init`, `redis-init`, `cassandra-init`, `neo4j-init`), qui attend que la base soit prête, la charge, puis s'arrête. Les scripts sont en Python (Lots 1, 3 et le job du Lot 4), en shell avec `redis-cli` et Lua (Lot 2), en CQL (Lot 3), en Cypher avec `cypher-shell` (Lot 4).

## Travail en binôme avec Git

- **Une branche par lot** (`lot1-catalogue`, `lot2-panier-sessions`…), fusionnée dans `main` quand le lot est relu par l'autre membre.
- **Des commits petits et fréquents**, avec un message au présent qui dit ce que le commit apporte (le sujet pénalise un commit unique).
- **Avant de fusionner :** `docker compose down -v` puis `docker compose up -d`, et toutes les requêtes types du lot doivent passer.
- **État actuel :** les branches sont empilées, chacune partant de la précédente : `main`, `lot1-catalogue`, `lot2-panier-sessions`, `lot3-logs-analytics`, `lot1-python` (scripts du Lot 1 en Python), `api-prototype`, `lot4-recommandations`. La dernière contient donc tout le projet. Rien n'est encore poussé ni fusionné dans `main`.

## Grille de rédaction d'un lot (document de conception)

1. Requêtes métier (tableau : requête, fréquence, exigence)
2. Choix technologique, avec les alternatives écartées et pourquoi
3. Modèle de données, avec un exemple réel
4. Patterns ou règles de modélisation appliqués
5. Index, ou clés de partition et de clustering
6. Placement CAP et réglages de cohérence
7. Compromis assumés et limites
8. Requêtes types avec leur résultat

## Vocabulaire commun

Les termes techniques employés dans les documents, avec le sens retenu dans ce projet.

| Terme | Sens dans ce projet | Lot |
|---|---|---|
| Persistance polyglotte | Une base différente par besoin, chacune choisie pour sa façon d'accéder aux données | Tous |
| Query-Driven | On liste d'abord les requêtes, puis on dessine le modèle qui les sert ; en Cassandra, une table par requête | Tous, surtout 3 |
| CAP | En cas de coupure réseau, une base distribuée choisit entre rester cohérente (CP) et rester disponible (AP) | Tous |
| SKU | Identifiant produit commun aux quatre bases, `PREFIXE-000000` | Tous |
| Replica set | Un primaire qui reçoit les écritures et des secondaires qui le copient ; élection automatique si le primaire tombe | 1 |
| `writeConcern: majority` | Une écriture n'est confirmée qu'une fois copiée sur la majorité des nœuds | 1 |
| Extended Reference | Recopier quelques champs d'un objet lié (nom de la marque) pour éviter une jointure | 1 |
| Subset | N'embarquer qu'une partie d'une liste (les 5 avis récents), le reste dans une autre collection | 1 |
| Computed | Stocker un résultat déjà calculé (note moyenne, nombre d'avis) au lieu de le recalculer à chaque lecture | 1 |
| Attribute pattern | Ranger des caractéristiques variables en paires `{k, v, u}` pour pouvoir les indexer toutes avec un seul index | 1 |
| TTL glissant | Une durée de vie relancée à chaque utilisation : la donnée expire après une période d'inactivité | 2, 3 |
| Fonction Lua (Redis) | Code exécuté dans Redis en une seule opération atomique : aucune autre commande ne s'intercale | 2 |
| Outbox | L'événement est écrit dans la même opération que la modification : impossible d'avoir l'un sans l'autre | 2 |
| Groupe de consommateurs | Lecture d'un Stream avec acquittement : un événement non acquitté est relu après une panne | 2, 3 |
| Clé de partition | Choisit le nœud qui stocke une ligne et regroupe les lignes lues ensemble | 3 |
| Colonne de clustering | Trie les lignes à l'intérieur d'une partition, sur le disque | 3 |
| Bucket, shard | Découpage volontaire d'une partition trop chargée en 16 morceaux connus à l'avance | 3 |
| Tombstone | Marque de suppression ; trop de tombstones ralentissent les lectures (d'où `UNSET` plutôt que `null`) | 3 |
| Compteur (`COUNTER`) | Colonne qu'on incrémente sans la lire ; ni idempotente ni compatible avec un TTL | 3 |
| `LOCAL_ONE`, `LOCAL_QUORUM` | Nombre de réplicas qui doivent confirmer : un seul, ou la majorité (2 sur 3) | 3 |
| Nœud, arête, propriété | Les objets du graphe, les relations entre eux, et les valeurs portées par l'un ou l'autre | 4 |
| Interface de compatibilité | Nœud partagé (socket, type de mémoire, format) qu'un produit exige et qu'un autre fournit | 4 |
| Supernœud | Nœud relié à des milliers d'autres (la catégorie mémoire, l'interface DDR5) : un parcours ne doit pas le traverser en entier | 4 |
| Arête précalculée | Relation calculée par un job (`BOUGHT_WITH`), lue directement au lieu d'être recalculée à chaque affichage | 4 |
| Raft | Protocole de consensus du cluster Neo4j : une écriture est validée par la majorité des primaires | 4 |
