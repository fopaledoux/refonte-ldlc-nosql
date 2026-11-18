// =============================================================================
// Lot 4 — Catalogue : catégories, marques, gammes, produits, interfaces de compatibilité
// Source : export du Lot 1 (data/produits.tsv, data/categories.tsv). Idempotent (MERGE).
//
// Règle de modélisation : une information que l'on PARCOURT devient un nœud
// (marque, gamme, catégorie, socket, puce graphique) ; une information que l'on
// AFFICHE ou que l'on compare reste une propriété (nom, prix).
// =============================================================================

// --- Arbre des catégories (32, dont 7 nœuds intermédiaires) -------------------
LOAD CSV WITH HEADERS FROM 'file:///categories.tsv' AS r FIELDTERMINATOR '\t'
MERGE (c:Category {id: r.id})
SET c.name = r.name;

LOAD CSV WITH HEADERS FROM 'file:///categories.tsv' AS r FIELDTERMINATOR '\t'
WITH r WHERE r.parent <> ''
MATCH (c:Category {id: r.id}), (parent:Category {id: r.parent})
MERGE (c)-[:SUBCATEGORY_OF]->(parent);

// --- Produits en vente, marque et catégorie ------------------------------------
// Neo4j n'a pas de type décimal : le prix est stocké en centimes (entier), jamais en flottant.
// « 451.5 » -> 45150 : partie entière x 100, plus les deux premières décimales (calcul exact).
LOAD CSV WITH HEADERS FROM 'file:///produits.tsv' AS r FIELDTERMINATOR '\t'
CALL {
  WITH r
  MERGE (p:Product {sku: r.sku})
  SET p.name = r.name, p.category = r.category, p.brand = r.brand,
      p.price_cents = toInteger(split(r.price, '.')[0]) * 100
                      + toInteger(left(coalesce(split(r.price, '.')[1], '') + '00', 2))
  WITH p, r
  MATCH (c:Category {id: r.category})
  MERGE (p)-[:IN_CATEGORY]->(c)
  WITH p, r WHERE r.brand <> ''
  MERGE (b:Brand {name: r.brand})
  MERGE (p)-[:MADE_BY]->(b)
} IN TRANSACTIONS OF 2000 ROWS;

// Un second label par type de composant : les motifs se lisent comme le métier
// (cpu:CPU)-[:REQUIRES]->(:Socket)<-[:PROVIDES]-(carte:Motherboard)
MATCH (p:Product {category: 'processeurs'}) SET p:CPU;
MATCH (p:Product {category: 'cartes-meres'}) SET p:Motherboard;
MATCH (p:Product {category: 'memoire-ram'}) SET p:Memory;
MATCH (p:Product {category: 'boitiers'}) SET p:Case;
MATCH (p:Product {category: 'cartes-graphiques'}) SET p:GPU;
MATCH (p:Product {category: 'alimentations'}) SET p:PowerSupply;

// --- Interfaces de compatibilité -------------------------------------------------
// Un produit EXIGE (REQUIRES) une interface qu'un autre FOURNIT (PROVIDES) :
//   processeur  -REQUIRES-> socket       <-PROVIDES- carte mère
//   barrette    -REQUIRES-> type mémoire <-PROVIDES- carte mère
//   carte mère  -REQUIRES-> format       <-PROVIDES- boîtier (plusieurs formats par boîtier)
// Une seule règle de parcours couvre toutes les compatibilités, et un nouveau
// critère (M.2, longueur de carte graphique...) ajoute des nœuds, pas un schéma.
LOAD CSV WITH HEADERS FROM 'file:///produits.tsv' AS r FIELDTERMINATOR '\t'
WITH r WHERE r.socket <> ''
MATCH (p:Product {sku: r.sku})
MERGE (i:Interface:Socket {id: 'socket:' + r.socket})
  ON CREATE SET i.kind = 'socket', i.name = r.socket
FOREACH (_ IN CASE WHEN r.category = 'processeurs' THEN [1] ELSE [] END | MERGE (p)-[:REQUIRES]->(i))
FOREACH (_ IN CASE WHEN r.category = 'cartes-meres' THEN [1] ELSE [] END | MERGE (p)-[:PROVIDES]->(i));

LOAD CSV WITH HEADERS FROM 'file:///produits.tsv' AS r FIELDTERMINATOR '\t'
WITH r WHERE r.memory_type <> ''
MATCH (p:Product {sku: r.sku})
MERGE (i:Interface:MemoryType {id: 'memory:' + r.memory_type})
  ON CREATE SET i.kind = 'memory', i.name = r.memory_type
FOREACH (_ IN CASE WHEN r.category = 'memoire-ram' THEN [1] ELSE [] END | MERGE (p)-[:REQUIRES]->(i))
FOREACH (_ IN CASE WHEN r.category = 'cartes-meres' THEN [1] ELSE [] END | MERGE (p)-[:PROVIDES]->(i));

LOAD CSV WITH HEADERS FROM 'file:///produits.tsv' AS r FIELDTERMINATOR '\t'
WITH r WHERE r.form_factor <> ''
MATCH (p:Product {sku: r.sku})
MERGE (i:Interface:FormFactor {id: 'form:' + r.form_factor})
  ON CREATE SET i.kind = 'form_factor', i.name = r.form_factor
MERGE (p)-[:REQUIRES]->(i);

LOAD CSV WITH HEADERS FROM 'file:///produits.tsv' AS r FIELDTERMINATOR '\t'
WITH r WHERE r.case_form_factors <> ''
MATCH (p:Product {sku: r.sku})
UNWIND split(r.case_form_factors, '|') AS format
MERGE (i:Interface:FormFactor {id: 'form:' + format})
  ON CREATE SET i.kind = 'form_factor', i.name = format
MERGE (p)-[:PROVIDES]->(i);

// --- Puce graphique : les cartes d'une même puce sont des alternatives ------------
LOAD CSV WITH HEADERS FROM 'file:///produits.tsv' AS r FIELDTERMINATOR '\t'
WITH r WHERE r.chipset <> ''
MATCH (p:Product {sku: r.sku})
MERGE (c:Chipset {name: r.chipset})
MERGE (p)-[:USES_CHIP]->(c);

// --- Gammes (ROG, TUF, Vengeance, Ryzen...) -----------------------------------
// Déduites du nom : le mot qui suit la marque (« Asus ROG Astral OC » -> Asus ROG).
// Retenues seulement si au moins 3 produits la partagent et si le mot n'est pas un
// code de modèle (chiffres) : une gamme d'un seul produit n'apporte aucun parcours.
MATCH (p:Product)-[:MADE_BY]->(b:Brand)
WHERE p.name STARTS WITH b.name + ' '
WITH p, b, split(substring(p.name, size(b.name) + 1), ' ')[0] AS mot
WHERE mot =~ '[A-Za-z][A-Za-z!+-]+'
WITH b, mot, collect(p) AS produits
WHERE size(produits) >= 3
MERGE (s:Series {id: b.name + ' ' + mot})
  ON CREATE SET s.name = mot
MERGE (s)-[:OF_BRAND]->(b)
WITH s, produits
UNWIND produits AS p
MERGE (p)-[:IN_SERIES]->(s);
