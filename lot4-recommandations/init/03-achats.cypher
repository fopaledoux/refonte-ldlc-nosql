// =============================================================================
// Lot 4 — Clients, commandes et vues (export du Lot 3). Idempotent (MERGE, SET).
//
// La commande reste un nœud : « achetés ensemble » veut dire « dans le même
// panier », pas seulement « par le même client ».
// =============================================================================

LOAD CSV WITH HEADERS FROM 'file:///commandes.tsv' AS r FIELDTERMINATOR '\t'
CALL {
  WITH r
  MERGE (c:Customer {id: r.customer_id})
  MERGE (o:Order {id: r.order_id})
  SET o.at = datetime(r.ordered_at), o.status = r.status
  MERGE (c)-[:PLACED]->(o)
  WITH o, r
  MATCH (p:Product {sku: r.sku})
  MERGE (o)-[l:CONTAINS]->(p)
  SET l.quantity = toInteger(r.quantity),
      l.unit_price_cents = toInteger(split(r.unit_price, '.')[0]) * 100
                           + toInteger(left(coalesce(split(r.unit_price, '.')[1], '') + '00', 2))
} IN TRANSACTIONS OF 5000 ROWS;

MATCH (o:Order)-[l:CONTAINS]->()
WITH o, sum(l.quantity * l.unit_price_cents) AS total
SET o.total_cents = total;

// Vues de fiches produit par les clients connectés, agrégées sur 7 jours :
// un signal d'intérêt plus faible qu'un achat, mais beaucoup plus fréquent.
LOAD CSV WITH HEADERS FROM 'file:///vues.tsv' AS r FIELDTERMINATOR '\t'
CALL {
  WITH r
  MATCH (p:Product {sku: r.sku})
  MERGE (c:Customer {id: r.customer_id})
  MERGE (c)-[v:VIEWED]->(p)
  SET v.count = toInteger(r.views), v.last_at = datetime(r.last_viewed_at)
} IN TRANSACTIONS OF 5000 ROWS;
