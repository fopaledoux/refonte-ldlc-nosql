// =============================================================================
// Lot 4 — Recommandations : contraintes et index (Neo4j 5.26)
//
// Une contrainte d'unicité crée aussi l'index qui la vérifie : chaque requête
// démarre par une recherche indexée (un SKU, un client), puis parcourt des arêtes.
// Dans un graphe, suivre une arête ne coûte pas une recherche d'index : chaque
// nœud pointe directement vers ses relations (« index-free adjacency »).
// =============================================================================

// Catalogue (copie du Lot 1 : identifiants, nom, attributs de compatibilité)
CREATE CONSTRAINT product_sku IF NOT EXISTS FOR (p:Product) REQUIRE p.sku IS UNIQUE;
CREATE CONSTRAINT category_id IF NOT EXISTS FOR (c:Category) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT brand_name IF NOT EXISTS FOR (b:Brand) REQUIRE b.name IS UNIQUE;
CREATE CONSTRAINT series_id IF NOT EXISTS FOR (s:Series) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT chipset_name IF NOT EXISTS FOR (c:Chipset) REQUIRE c.name IS UNIQUE;
// Interfaces de compatibilité : socket, type de mémoire, format de carte mère
CREATE CONSTRAINT interface_id IF NOT EXISTS FOR (i:Interface) REQUIRE i.id IS UNIQUE;

// Achats et navigation (copie du Lot 3)
CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (c:Customer) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT order_id IF NOT EXISTS FOR (o:Order) REQUIRE o.id IS UNIQUE;

// Filtre par catégorie dans les recommandations (« une carte mère », « de la mémoire »)
CREATE INDEX product_category IF NOT EXISTS FOR (p:Product) ON (p.category);
