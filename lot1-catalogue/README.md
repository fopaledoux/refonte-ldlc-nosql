# Lot 1 - Catalogue produits (MongoDB)

**Statut : terminé et testé.** Conception et justifications : [`docs/conception.md`](../docs/conception.md#2-lot-1--catalogue-produits-mongodb).

## En bref

| | |
|---|---|
| Technologie | MongoDB 8, base `ldlc_catalogue` ; scripts en Python 3.11 avec pymongo 4.10 |
| Collections | `products` (66 778 fiches), `reviews` (96 236 avis), `categories` (32), `brands` (687) |
| Patterns | Polymorphic, Attribute, Extended Reference, Subset, Computed, Tree, Schema Versioning |
| Données | PC Part Dataset (MIT) + avis synthétiques, voir [`data/raw/pc-part-dataset/SOURCE.md`](data/raw/pc-part-dataset/SOURCE.md) |

## Lancer

Seul Docker est nécessaire. Depuis la racine du dépôt :

```bash
docker compose up -d
```

Le conteneur ponctuel `mongo-init` (Python et pymongo, image construite depuis `Dockerfile`) exécute les scripts de `init/` dans l'ordre, puis s'arrête (environ 20 secondes). Il ne charge que si la base est vide, comme au premier démarrage. Pour suivre le chargement :

```bash
docker compose logs -f mongo-init
```

Le chargement est terminé quand la ligne `[lot1] OK` apparaît.

## Exécuter les requêtes types

Les requêtes types sont des scripts Python, lancés dans le même conteneur que le chargement :

```bash
docker compose run --rm mongo-init python /lot1/queries/01-lectures.py
```

```bash
docker compose run --rm mongo-init python /lot1/queries/02-ecritures.py
```

```bash
docker compose run --rm mongo-init python /lot1/queries/03-evolutions-schema.py
```

```bash
docker compose run --rm mongo-init python /lot1/queries/04-insertions-modifications.py
```

Chaque requête affiche son résultat et son plan d'exécution (index utilisé, clés et documents lus).

**Les écritures s'appliquent directement à la base et y restent** (`02-ecritures.py`, `04-insertions-modifications.py`, `03-evolutions-schema.py`) : on peut les vérifier dans Compass, et chaque script affiche à la fin les filtres à coller. Les scripts 02 et 04 repartent de l'état initial à chaque exécution, donc relancer donne le même résultat, sans empiler les modifications. Pour les retirer :

```bash
docker compose run --rm mongo-init python /lot1/queries/02-ecritures.py --annuler
```

```bash
docker compose run --rm mongo-init python /lot1/queries/04-insertions-modifications.py --annuler
```

Tant qu'elles sont en place, les lectures (`01-lectures.py`) en tiennent compte : par exemple, la fiche MB-000131 affiche 22 avis au lieu de 21, et les kits Corsair DDR4 sont à -10 %.

> **Sous Git Bash (Windows)**, préfixer par `MSYS_NO_PATHCONV=1`, sinon le chemin `/lot1/...` est converti en chemin Windows. PowerShell, macOS et Linux ne sont pas concernés.

Pour explorer avec **MongoDB Compass**, se connecter à `mongodb://localhost:27018`.

Pour **recharger le catalogue** sans toucher aux autres lots :

```bash
docker compose run --rm -e FORCER=1 mongo-init
```

Pour **repartir de zéro** (toutes les bases effacées puis rechargées) :

```bash
docker compose down -v
```

```bash
docker compose up -d
```

## Contenu

| Fichier | Rôle |
|---|---|
| `Dockerfile` | Image des scripts : Python 3.11 et pymongo 4.10.1 (l'image `mongo:8` ne contient pas Python) |
| `init/charger.py` | Lance les cinq scripts ci-dessous, seulement si la base est vide (ou avec `FORCER=1`) |
| `init/01-schema.py` | Collections et validateurs `$jsonSchema` (tronc commun, bornes des tableaux) |
| `init/02-import-catalogue.py` | Transformation du jeu public en fiches produits, catégories et marques |
| `init/03-avis-synthetiques.py` | Génération déterministe des avis, puis résumé embarqué (note + 5 derniers avis) |
| `init/04-compatibilites.py` | Liste courte de produits compatibles (socket, format), recalculable ensuite par le Lot 4 |
| `init/05-index.py` | Index, chacun justifié par une requête type |
| `lib/catalogue_config.py` | Arborescence des catégories, préfixes de SKU, gabarits d'attributs, règles de normalisation |
| `lib/pipelines.py` | Pipeline de résumé des avis, partagé par l'import, les écritures et la réconciliation |
| `lib/outils.py` | Connexion, affichage des résultats et des plans d'exécution, règles de conversion des nombres |
| `lib/exports.py` | Exports vers les autres lots : SKU vendables et limites d'achat (Lot 2), produits avec leurs attributs de compatibilité (Lots 3 et 4), catégories (Lot 4), toujours depuis une copie temporaire du catalogue à l'état initial |
| `queries/01-lectures.py` | R1 à R11 : fiche, listing, facettes, avis, recherche, marque, meilleures notes, back-office, mesures |
| `queries/02-ecritures.py` | E1 à E7 : avis et modération, promotion, renommages, fin de commercialisation, réconciliation (restent dans la base ; `--annuler`) |
| `queries/03-evolutions-schema.py` | V1 à V4 : nouvelle catégorie, nouvel attribut, Schema Versioning, garde-fous |
| `queries/04-insertions-modifications.py` | I1 à I5 : création de fiche et compteurs, lot ordonné ou non, upsert, doublons refusés, import rejouable. U1 à U8 : `$set`, `$inc`, opérateurs de tableau, modification en masse, mise à jour conditionnelle, remplacement complet, anonymisation RGPD (restent dans la base ; `--annuler`) |

### Exports vers les autres lots

Les fichiers de données des Lots 2, 3 et 4 viennent du catalogue. Une seule commande, depuis la racine du dépôt, régénère les cinq :

```bash
docker compose run --rm mongo-init python /lot1/lib/exports.py
```

Tout est automatique, en environ 20 secondes :
1. le catalogue est chargé dans une base temporaire (`ldlc_catalogue_export`) par les mêmes scripts qu'au premier démarrage, donc à l'état initial ;
2. les cinq fichiers sont écrits dans `lot2-panier-sessions/data/` (SKU vendables, limites d'achat), `lot3-logs-analytics/data/` et `lot4-recommandations/data/` (produits, catégories), et la commande indique pour chacun s'il est inchangé ou mis à jour ;
3. la base temporaire est supprimée.

La base de travail `ldlc_catalogue` n'est pas touchée : les écritures des scripts 02, 03 et 04 y restent, et ne se retrouvent jamais dans les exports. Il n'y a rien à annuler avant. Pour un seul export, sur la sortie standard : `docker compose run --rm -T mongo-init python /lot1/lib/exports.py skus` (ou `limites`, `produits`, `categories`).

L'export des produits ajoute les attributs de compatibilité utilisés par les paniers du Lot 3 et par le graphe du Lot 4 : socket (processeurs, cartes mères), type de mémoire (barrettes, cartes mères), format de la carte mère et formats acceptés par le boîtier, puce des cartes graphiques. Le type de mémoire d'une carte mère manque au jeu source. Il est déduit de son nom (« DDR4 », ou l'abréviation « D4 » / « D5 » des noms Asus et ASRock), sinon de son socket : 928 cartes sur 951 en reçoivent un.

### Liste des compatibles recalculée par le Lot 4

Le job `lot4-recommandations/jobs/compatibles_vers_lot1.py` réécrit `compatibleProducts` à partir du graphe : il ajoute le type de mémoire et classe les produits par les paniers réels. Comme les écritures de démonstration, ses modifications restent dans la base ; il copie d'abord les listes du Lot 1 dans la collection `compatibleProductsBackup`, et `--annuler` les remet à l'identique. Les commandes sont dans le README du Lot 4.

### Passage de JavaScript à Python

Les scripts étaient d'abord écrits pour `mongosh` (JavaScript). Leur version Python produit **exactement les mêmes données** : même empreinte des quatre collections, types BSON compris, mêmes validateurs et mêmes index ; mêmes résultats pour les 22 requêtes types de l'époque ; les trois exports régénèrent à l'octet près les fichiers des Lots 2 et 3. Trois règles le garantissent (`lib/outils.py`) :
- un nombre entier lu dans le jeu source (`4.0`) est stocké comme entier 32 bits, comme le faisait `mongosh` ;
- les arrondis prennent les demis vers le haut (`round(2.5)` donnerait 2 en Python, 3 en JavaScript) ;
- le générateur pseudo-aléatoire des avis (`mulberry32`) calcule sur des entiers de 32 bits, avec la même graine.

## Résultats mesurés (extrait)

| Requête | Index | Lu | Renvoyé |
|---|---|---|---|
| R1 fiche produit par slug | `ux_slug` | 1 clé, 1 document | 1 |
| R2 listing « Refroidissement » trié par prix | `ix_listing_prix` | 5 clés, 5 documents, sans tri en mémoire | 5 |
| R3 processeurs AM5, au moins 8 cœurs, TDP <= 120 W | `ix_facettes` | 25 clés, 25 documents | 11 |
| R7 kits Corsair DDR5 32 Go par prix | `ix_marque` | 154 documents (1 256 avec la première version de l'index) | 5 |
| R11 fiche du best-seller | — | 2,2 Ko au lieu de 390 Ko si les 1 163 avis étaient embarqués | — |
| U5 -10 % sur les kits Corsair DDR4 (`update_many` + pipeline) | `ix_marque` | 536 clés, 536 documents ; 221 fiches modifiées en 30 à 120 ms | — |
| U8 anonymisation d'un client | `ix_avis_client`, `recentReviews.reviewId` | 5 avis dans `reviews` + 2 fiches qui en recopient un | — |
