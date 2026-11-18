# Schéma d'architecture globale

> Les quatre lots sont conçus et testés. Les schémas montrent le déploiement de production ; en développement, chaque base tourne en un seul conteneur (`docker-compose.yml`).
> Les diagrammes sont en Mermaid : GitHub les affiche directement.

## 1. Vue d'ensemble : persistance polyglotte

```mermaid
flowchart TB
  U["Clients<br/>navigateur, appli mobile"] --> LB["CDN + répartiteur de charge"]
  LB --> API["Serveurs web / API<br/>sans état, N instances"]

  subgraph L1["Lot 1 · Catalogue"]
    M[("MongoDB<br/>replica set 3 nœuds")]
  end
  subgraph L2["Lot 2 · Panier et sessions"]
    R[("Redis<br/>primaire + 2 réplicas")]
  end
  subgraph L4["Lot 4 · Recommandations"]
    N[("Neo4j<br/>cluster 3 primaires")]
  end
  BUS{{"Bus d'événements<br/>+ ingestion (agrégation)"}}
  subgraph L3["Lot 3 · Logs et analytics"]
    C[("Cassandra<br/>3 nœuds, RF 3")]
    JOB["Job de classement<br/>(horaire)"]
  end
  OMS["Système de commandes<br/>(ACID, hors périmètre)"]

  API -- "fiches, listings,<br/>facettes, avis" --> M
  API -- "panier, session<br/>(TTL)" --> R
  API -- "recommandations" --> N
  API -- "vues, historique de commandes,<br/>classements" --> C
  API -- "pages vues, clics,<br/>recherches" --> BUS
  API -- "commande" --> OMS

  M -- "Change Streams :<br/>produit créé / modifié, prix" --> BUS
  M -- "SKU vendables,<br/>limites d'achat" --> R
  R -- "events:cart<br/>(outbox)" --> BUS
  OMS -- "commande passée,<br/>statut" --> BUS
  BUS -- "événements, compteurs,<br/>prix, commandes" --> C
  C <-- "vues par jour -> rankings" --> JOB
  BUS -- "produits, commandes,<br/>vues des clients" --> N
  N -. "liste courte des compatibles<br/>(10 max, job nocturne)" .-> M
```

| Flux | Nature | Cohérence |
|---|---|---|
| API → MongoDB | Lecture de fiches et listings (secondaires), écritures back-office (primaire, `w: majority`) | Forte à l'écriture, à terme en lecture publique |
| API → Redis | Session lue à chaque requête, panier modifié par fonctions Lua atomiques (`FCALL`) | Forte pour le client : tout passe par le primaire |
| MongoDB → Redis | Réplique des SKU vendables et des limites d'achat, rechargée par bascule atomique (`RENAME`) | À terme |
| Redis → connecteur → Cassandra | Événements du panier écrits dans le Stream `events:cart` dans la même opération que la modification (outbox), lus par un groupe de consommateurs, acquittés après écriture | À terme, au moins une fois |
| API → bus → Cassandra | Un événement par clic (`LOCAL_ONE`), compteurs agrégés par lot de quelques secondes | À terme, perte d'un clic acceptée |
| Système de commandes → Cassandra | Commande passée, changements de statut ; deux tables écrites par BATCH journalisé (`LOCAL_QUORUM`) | Forte à la lecture (quorum) |
| MongoDB → Cassandra | Chaque changement de `price.amount` ajoute une ligne à l'historique des prix | À terme |
| Job → Cassandra | Classements recalculés toutes les heures, lus en une partition | Frais à l'heure |
| MongoDB → bus → Neo4j | Propagation des produits (SKU, nom, catégorie, marque, prix en centimes, attributs de compatibilité) | À terme (quelques secondes) |
| Cassandra → Neo4j | Commandes (nœuds `Order`) et vues des clients connectés (arêtes `VIEWED`) ; `BOUGHT_WITH` recalculé chaque nuit | À terme (un jour pour les arêtes précalculées) |
| Neo4j → MongoDB | Recalcul nocturne de `compatibleProducts` (10 au plus par fiche), classés par les paniers réels puis la popularité | À terme (vitrine, non critique) |

**Identifiant commun :** le SKU (`CPU-000001`, `MB-000131`…) est la clé partagée par les quatre bases (voir [`conventions.md`](conventions.md)).

**Prototype :** le dossier [`api/`](../api/) implémente le rôle « Serveurs web / API » de ce schéma pour les quatre lots. C'est une API FastAPI sans état, avec 23 routes, la session passée dans un en-tête et le panier modifié uniquement par les fonctions Lua du Lot 2. Les étapes 1 à 9 du parcours de la section 7 y sont exécutables de bout en bout (`api/demo.py`, 24 vérifications). Les étapes 10 à 12 relèvent du système de commandes, hors périmètre. Les étapes 13 et 14 sont jouées par les exports des Lots 1 et 3, le chargement du graphe et le job `compatibles_vers_lot1.py`.

### Où vit chaque donnée

Chaque donnée a **une seule source de vérité**. Les autres bases en gardent une copie adaptée à leurs requêtes et la reçoivent par un flux.

| Donnée | Source de vérité | Copies, et pourquoi |
|---|---|---|
| Fiche produit (nom, prix, attributs) | Lot 1, MongoDB | Lot 2 : SKU vendables et limites d'achat, pour contrôler un ajout au panier sans interroger MongoDB. Lot 3 : historique des prix (obligation Omnibus). Lot 4 : nœud `Product` et ses interfaces de compatibilité |
| Avis clients et note moyenne | Lot 1, MongoDB | Aucune : les 5 avis récents sont embarqués dans la fiche (Subset pattern) |
| Session, panier | Lot 2, Redis | Lot 3 : chaque modification du panier, comme événement (`events:cart`) |
| Clic, page vue, recherche | Lot 3, Cassandra (journal brut) | Lot 3 : compteurs et classements. Lot 4 : vues des clients connectés (`VIEWED`) |
| Commande | Système de commandes (ACID, hors périmètre) | Lot 3 : historique en lecture, deux tables. Lot 4 : nœuds `Order`, base de « achetés ensemble » |
| Compatibilité entre produits | Attributs du Lot 1 (socket, mémoire, format) | Lot 4 : interfaces `REQUIRES` / `PROVIDES`. Lot 1 : liste courte `compatibleProducts`, recalculée par le Lot 4 |
| « Achetés ensemble », recommandations | Calculées par le Lot 4, à partir des commandes | Aucune : reconstructibles à tout moment |

## 2. Parcours d'un client à travers les quatre bases

Un client connecté consulte une carte graphique, l'ajoute à son panier et commande. Chaque étape touche la base faite pour elle.

```mermaid
sequenceDiagram
  autonumber
  actor Client
  participant API as Serveurs web / API
  participant R as Redis (Lot 2)
  participant M as MongoDB (Lot 1)
  participant C as Cassandra (Lot 3)
  participant N as Neo4j (Lot 4)
  participant OMS as Système de commandes
  Client->>API: ouvre la fiche GPU-000009
  API->>R: session_touch (session, TTL glissant)
  API->>M: fiche par slug (1 document, avis récents embarqués)
  API->>C: vues du jour (1 ligne de compteur)
  API->>N: souvent achetés ensemble (20 arêtes au plus)
  API-->>C: événement product_view (journal, LOCAL_ONE)
  Client->>API: ajoute au panier
  API->>R: cart_add (vendable ? limite de 2 ? puis événement events:cart)
  R-->>C: connecteur : événement du panier, compteur cart_adds
  Client->>API: commande
  API->>OMS: paiement et stock (transaction ACID)
  OMS-->>C: commande dans orders_by_customer et orders_by_id (LOCAL_QUORUM)
  C-->>N: la nuit : nœud Order, arêtes BOUGHT_WITH recalculées
  N-->>M: la nuit : liste courte compatibleProducts de la fiche
```

| Étape | Base | Pourquoi celle-ci |
|---|---|---|
| 2 | Redis | La session est lue à chaque requête : il faut moins d'une milliseconde et une expiration automatique |
| 3 | MongoDB | La fiche a un schéma variable par catégorie et se lit en un seul document |
| 4, 6, 9 | Cassandra | Une écriture par clic, des millions par jour, avec des compteurs déjà calculés pour la lecture. L'événement du panier arrive par le connecteur |
| 5 | Neo4j | « Achetés ensemble » est un parcours de relations, borné par les arêtes précalculées |
| 8 | Redis | Une fonction Lua atomique contrôle et modifie le panier, et publie l'événement dans la même opération |
| 11 | Système de commandes | Paiement et stock exigent des transactions ACID : aucune des quatre bases n'en est la source de vérité |
| 12 à 14 | Cassandra, Neo4j, MongoDB | Copies dérivées, à terme : l'historique, les recommandations et la liste des compatibles suivent sans bloquer la commande |
