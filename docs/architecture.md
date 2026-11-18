# Schéma d'architecture globale

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

**Identifiant commun :** Le SKU (Stock Keeping Unit, « unité de gestion de stock ») est l’identifiant unique d’un produit vendu, celui qu’un commerçant utilise pour gérer son stock, son catalogue et ses ventes.
Dans ce projet, le SKU prend la forme PRÉFIXE-000000. Le préfixe indique la catégorie(voir [`conventions.md`](conventions.md)).
exemple: 
CPU-000001 : un processeur (AMD Ryzen 7 9800X3D) ;
MB-000227 : une carte mère (Gigabyte B650I AX) ;
GPU-000009 : une carte graphique (Asus ROG Astral OC, une RTX 5090) ;

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
