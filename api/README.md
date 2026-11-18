# Prototype d'API (bonus)

## Lancer

Depuis la racine du dépôt :

```bash
docker compose up -d
```

L'API démarre une fois les quatre lots chargés, sur [http://localhost:8000](http://localhost:8000). La documentation interactive (Swagger), où chaque route peut être essayée, est sur [http://localhost:8000/docs](http://localhost:8000/docs). Elle charge ses feuilles de style depuis un CDN : le navigateur doit avoir accès à Internet.

La démonstration de bout en bout enchaîne un parcours client et vérifie chaque réponse :

```bash
docker compose exec api python /api/demo.py
```

> **Sous Git Bash (Windows)**, préfixer par `MSYS_NO_PATHCONV=1`. PowerShell, macOS et Linux ne sont pas concernés.

## Routes

| Méthode | Route | Base | Rôle |
|---|---|---|---|
| GET | `/health` | les quatre | État et temps de réponse de chaque base |
| GET | `/categories` | MongoDB | Arbre des catégories |
| GET | `/categories/{id}/products` | MongoDB | Listing de la catégorie et de ses sous-catégories, filtres `spec=socket:AM5`, `spec=core_count>=8`, `max_price`, tri par prix ou par note, pagination par curseur |
| GET | `/categories/{id}/facets` | MongoDB | Nombre de produits par valeur de filtre |
| GET | `/search?q=` | MongoDB | Recherche plein texte |
| GET | `/products/{slug}` | MongoDB, puis Cassandra | Fiche complète en une lecture ; la vue est enregistrée (journal, parcours, compteurs) et le nombre de vues du jour est renvoyé |
| GET | `/products/{sku}/reviews` | MongoDB | Avis au-delà des 5 embarqués |
| POST | `/sessions` | Redis | Ouvre une session de visiteur et son panier invité ; renvoie l'identifiant à passer dans `X-Session-Id` |
| GET | `/sessions/me` | Redis | Lit la session et prolonge sa durée de vie |
| POST | `/sessions/login` | Redis | Connexion : nouvel identifiant de session, panier invité fusionné dans le panier client |
| POST | `/sessions/logout` | Redis | Supprime la session ; le panier client reste |
| GET | `/cart` | Redis, puis MongoDB | Panier, avec nom et prix courant lus dans le catalogue en une requête |
| POST | `/cart/items` | Redis | Ajoute un produit : refusé s'il n'est pas en vente, plafonné par la limite d'achat |
| PUT | `/cart/items/{sku}` | Redis | Fixe la quantité d'une ligne (0 la retire) |
| DELETE | `/cart` | Redis | Vide le panier, avec l'événement publié dans la même transaction |
| GET | `/products/{sku}/views` | Cassandra | Vues et ajouts au panier du jour et des 7 derniers jours |
| GET | `/rankings/products?period=week` | Cassandra | Top des produits consultés (jour, semaine, 7 jours glissants ; global ou par catégorie) |
| GET | `/customers/{id}/orders` | Cassandra | Historique des commandes, la plus récente d'abord, paginé (`next`) |
| GET | `/orders/{id}` | Cassandra | Une commande et l'historique de ses statuts |
| GET | `/products/{sku}/price-history` | Cassandra | Historique des prix et prix de référence légal d'une promotion (le plus bas des 30 jours) |
| GET | `/products/{sku}/recommendations` | Neo4j, puis MongoDB | Souvent achetés ensemble (arêtes `BOUGHT_WITH`). Sans historique d'achat : les produits compatibles. Neo4j indisponible : la liste `compatibleProducts` du Lot 1 |
| GET | `/products/{sku}/compatible?category=` | Neo4j, puis MongoDB | Produits compatibles par les interfaces (socket, type de mémoire, format), classés par paniers communs puis par popularité |
| GET | `/cart/recommendations` | Redis, Neo4j, MongoDB | Compléter le panier : suggestions compatibles avec **tout** le panier (une barrette DDR5 pour une carte DDR5, un boîtier qui accepte son format) |
| GET | `/customers/{id}/recommendations` | Neo4j, puis MongoDB | Recommandations personnalisées à partir des achats du client, avec leur raison (« parce qu'il a acheté… ») |

## Contenu

| Fichier | Rôle |
|---|---|
| `app/main.py` | Application FastAPI, route `/health` |
| `app/catalogue.py` | Routes du catalogue (MongoDB) |
| `app/session.py` | Routes de session et de panier (Redis) |
| `app/analytics.py` | Routes de vues, classements, commandes et prix (Cassandra), et écriture d'une vue |
| `app/recommandations.py` | Routes de recommandation et de compatibilité (Neo4j), repli sur le Lot 1 |
| `app/bases.py` | Connexions, paramètres partagés avec les lots, mise en forme JSON |
| `demo.py` | Parcours client de bout en bout, avec 24 vérifications |
| `Dockerfile`, `requirements.txt` | Image Python 3.11 : FastAPI 0.115, Uvicorn 0.32, pymongo 4.10, redis 5.2, cassandra-driver 3.29 |
