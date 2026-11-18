# Lot 4 — Recommandations produits (Neo4j)

**Statut : terminé et testé.** Conception et justifications : [`docs/conception.md`](../docs/conception.md#5-lot-4--recommandations-neo4j).

## En bref

| | |
|---|---|
| Technologie | Neo4j 5.26 LTS, édition Community (graphe de propriétés, requêtes Cypher) ; une instance en développement, un cluster de 3 primaires en production |
| Nœuds | `Product` (avec un second label `CPU`, `Motherboard`, `Memory`, `Case`, `GPU`, `PowerSupply`), `Category`, `Brand`, `Series` (gamme), `Chipset`, `Interface` (`Socket`, `MemoryType`, `FormFactor`), `Customer`, `Order` |
| Arêtes | `IN_CATEGORY`, `SUBCATEGORY_OF`, `MADE_BY`, `IN_SERIES`, `OF_BRAND`, `USES_CHIP`, `REQUIRES` / `PROVIDES` (compatibilité), `PLACED`, `CONTAINS`, `VIEWED`, `BOUGHT_WITH` (précalculée) |
| Données | Les 16 222 produits en vente du Lot 1 ; 23 091 commandes de 8 726 clients et 13 926 couples client-produit vus par des clients connectés, exportés du Lot 3 (instantanés TSV dans `data/`) |
| Taille | 51 871 nœuds, 151 042 arêtes, chargés en 1 min 30 à 1 min 45 |
| Sortie vers le Lot 1 | La liste courte `compatibleProducts` de 5 157 fiches (job `jobs/compatibles_vers_lot1.py`) |

## Lancer

Depuis la racine du dépôt :

```bash
docker compose up -d
```

Le Lot 4 se lance aussi seul, car ses données sont des instantanés versionnés :

```bash
docker compose up -d neo4j-init
```

Le conteneur `neo4j-init` crée les contraintes, charge le graphe et calcule les arêtes `BOUGHT_WITH`, puis s'arrête. Comme pour les Lots 1 et 3, un graphe déjà chargé n'est pas rechargé :

```bash
docker compose logs -f neo4j-init
```

Pour recharger le graphe (vidé, puis rechargé depuis `data/`) :

```bash
docker compose run --rm -e FORCER=1 neo4j-init
```

**Neo4j Browser :** [http://localhost:7475](http://localhost:7475), connexion `bolt://localhost:7688`, sans authentification (développement uniquement). Pour voir le graphe autour d'un processeur, y coller par exemple :

```cypher
MATCH (cpu:CPU {sku: 'CPU-000054'})-[r:REQUIRES|BOUGHT_WITH]->(x) RETURN cpu, r, x
```

## Exécuter les requêtes types

```bash
docker compose exec neo4j sh /lot4/queries/01-recommandations.sh
```

```bash
docker compose exec neo4j sh /lot4/queries/02-mesures.sh
```

Chaque requête Cypher est affichée avec ses paramètres, avant son résultat. Les deux scripts sont en lecture seule et durent environ une minute chacun : chaque requête lance `cypher-shell`, un programme Java.

Le job vers le Lot 1 recalcule la liste courte `compatibleProducts` de chaque processeur, carte mère, barrette et boîtier, puis l'écrit dans MongoDB. Il affiche trois fiches avant et après :

```bash
docker compose run --rm lot4-jobs python /lot4/jobs/compatibles_vers_lot1.py
```

Comme les écritures de démonstration du Lot 1, le résultat reste dans la base. Avant sa première écriture, le job copie les listes du Lot 1 dans la collection `compatibleProductsBackup`. Pour les remettre à l'identique :

```bash
docker compose run --rm lot4-jobs python /lot4/jobs/compatibles_vers_lot1.py --annuler
```

Le service `lot4-jobs` n'est jamais lancé par `docker compose up` : il reprend l'image Python du Lot 1 et interroge Neo4j par son API HTTP.

> **Sous Git Bash (Windows)**, préfixer ces commandes par `MSYS_NO_PATHCONV=1`. PowerShell, macOS et Linux ne sont pas concernés.

## Régénérer les données

Les quatre fichiers de `data/` viennent des Lots 1 et 3. Chacun se régénère en une commande, depuis la racine du dépôt, puis le graphe se recharge :

```bash
docker compose run --rm mongo-init python /lot1/lib/exports.py
```

```bash
docker compose run --rm cassandra-init python3 /lot3/jobs/export_graphe.py
```

```bash
docker compose run --rm -e FORCER=1 neo4j-init
```

Le premier export écrit `produits.tsv` et `categories.tsv` à partir du catalogue à l'état initial. Le second écrit `commandes.tsv` et `vues.tsv` à partir de Cassandra. Chaque commande indique, fichier par fichier, s'il est inchangé ou mis à jour.

## Contenu

| Fichier | Rôle |
|---|---|
| `init/01-schema.cypher` | Contraintes d'unicité (et leurs index), index sur la catégorie |
| `init/02-catalogue.cypher` | Catégories, marques, gammes, produits, interfaces de compatibilité, puces graphiques |
| `init/03-achats.cypher` | Clients, commandes et lignes de commande, vues des clients connectés |
| `init/04-precalculs.cypher` | Popularité (`orders`) et arêtes `BOUGHT_WITH` : les 20 meilleurs partenaires de chaque produit. C'est le job nocturne de production |
| `init/charger.sh` | Chargement (conteneur `neo4j-init`), décompte des nœuds et des arêtes ; `FORCER=1` recharge |
| `lib/commun.sh` | Affichage des requêtes et des résultats, bilan des `PROFILE` |
| `lib/graphe.py` | Requête Cypher depuis Python par l'API HTTP de Neo4j, sans dépendance |
| `queries/01-recommandations.sh` | R1 à R11 : achetés ensemble, compatibilité, configuration complète, panier à compléter, recommandations personnalisées, amis d'amis, alternatives, gamme, explication |
| `queries/02-mesures.sh` | M1 à M6 : taille du graphe, interfaces contre arêtes directes, supernœuds, coût des parcours |
| `jobs/compatibles_vers_lot1.py` | `compatibleProducts` calculé dans le graphe, puis écrit dans MongoDB (`--annuler` pour revenir aux listes du Lot 1) |
| `data/produits.tsv`, `data/categories.tsv` | Produits en vente et arbre des catégories, exportés du Lot 1 |
| `data/commandes.tsv`, `data/vues.tsv` | Lignes de commande et vues des clients connectés sur 7 jours, exportées du Lot 3 |

## Questions du sujet et décisions

| Question | Décision |
|---|---|
| Quels nœuds ? | **Ce que l'on parcourt devient un nœud,** ce que l'on affiche reste une propriété. Marque, gamme, catégorie, puce et interfaces sont des nœuds ; nom et prix sont des propriétés |
| Compatibilité : arête directe ou nœud intermédiaire ? | **Nœuds d'interface :** un produit `REQUIRES` un socket, un type de mémoire ou un format qu'un autre `PROVIDES`. 8 808 arêtes, au lieu de 1 861 130 (211 fois plus) en reliant directement les produits compatibles |
| « Achetés ensemble » : à la volée ou précalculé ? | **Précalculé** chaque nuit : 20 arêtes `BOUGHT_WITH` au plus par produit, score cosinus, au moins 2 paniers communs. La fiche lit 20 arêtes, quelle que soit la popularité du produit |
| Profondeur des parcours ? | 1 saut pour la fiche (R1), 2 sauts d'interface pour la compatibilité (R3), 4 sauts pour « amis d'amis » (R7), bornés à 25 clients voisins |
| Supernœuds ? | Les parcours partent d'un produit et passent par l'interface ciblée, jamais par la catégorie ou la marque. « Achetés ensemble » est borné, R7 pondère par 1/log(popularité) |
| Synchronisation avec le catalogue ? | Le graphe est une **copie** : SKU, nom, catégorie, marque, prix en centimes et attributs de compatibilité. En production, il suit les Change Streams du Lot 1 et le flux du Lot 3 ; ici, des instantanés versionnés |

## Résultats mesurés (portable, une instance Docker, cache chaud)

| Mesure | Résultat |
|---|---|
| Chargement complet | 91 à 104 s : contraintes 6 s, catalogue 26 à 44 s, commandes et vues 23 à 28 s, `BOUGHT_WITH` 8 à 10 s (4 216 arêtes) |
| « Achetés ensemble » du produit le plus vendu (M4) | à la volée : 2 492 accès à la base, 6 à 21 ms ; précalculé : 78 accès, 1 à 3 ms, et ce coût ne grandit pas avec les ventes |
| Recommandations d'un client (M5) | par produit (R6, arêtes précalculées) : 1 123 accès, 3 ms ; par client (R7, 4 sauts) : 18 574 accès, 21 à 28 ms |
| Barrettes compatibles avec une carte DDR5 (M6) | par la catégorie : 81 990 accès, 89 ms ; par l'interface DDR5 : 37 993 accès, 20 ms |
| Disque | 24 Mo pour la base, après un point de contrôle ; le journal de transactions du chargement occupe 258 Mo (rotation et purge automatiques) |
| Job vers le Lot 1 | 5 157 fiches : 48 s de calcul dans Neo4j, 3 s d'écriture groupée dans MongoDB ; `--annuler` redonne exactement l'empreinte des listes d'origine |

## Limites

1. **Des instantanés, pas un flux :** le graphe ne voit une commande ou un changement de prix qu'au rechargement suivant. En production, il suit les flux des Lots 1 et 3, et seules les arêtes `BOUGHT_WITH` attendent la nuit.
2. **Des paniers synthétiques :** le générateur du Lot 3 compose des montages aux pièces compatibles, des évolutions et des lots de périphériques. Sur environ 23 000 commandes, seuls 1 358 produits ont assez de paniers communs pour recevoir des arêtes `BOUGHT_WITH`. Les autres se rabattent sur la compatibilité (API) : c'est le démarrage à froid.
3. **Des attributs déduits :** le type de mémoire des cartes mères et la gamme sont déduits du nom, par des règles documentées. Les processeurs n'ont de socket que pour 265 sur 547 (limite du Lot 1).
4. **Édition Community :** une instance, sans cluster ni sauvegarde à chaud. La haute disponibilité demande l'édition Enterprise ou le service Aura.
