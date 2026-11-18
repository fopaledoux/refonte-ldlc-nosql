// =============================================================================
// Lot 4 — Propriétés et arêtes précalculées (job nocturne en production)
//
// « Achetés ensemble » se calcule à la volée (produit <- commande -> produit),
// mais un produit vendu des milliers de fois est un supernœud : le parcours
// lirait toutes ses commandes à chaque affichage de la fiche. L'arête
// BOUGHT_WITH garde, pour chaque produit, ses 20 meilleurs partenaires :
// la fiche lit 20 arêtes au plus, quelle que soit la popularité du produit.
// =============================================================================

// Popularité : nombre de commandes non annulées contenant le produit
MATCH (p:Product)
CALL {
  WITH p
  OPTIONAL MATCH (p)<-[:CONTAINS]-(o:Order)
  WHERE o.status <> 'cancelled'
  RETURN count(DISTINCT o) AS commandes
}
SET p.orders = commandes;

// Recalcul complet : on supprime les arêtes de la veille
MATCH ()-[r:BOUGHT_WITH]->()
CALL { WITH r DELETE r } IN TRANSACTIONS OF 10000 ROWS;

// Pour chaque produit, ses 20 partenaires les plus fréquents (au moins 2 paniers communs).
// score = cosinus : paniers communs / racine(commandes de p x commandes de q).
// Il pénalise les produits présents partout, qui seraient sinon recommandés avec tout.
MATCH (p:Product) WHERE p.orders >= 2
CALL {
  WITH p
  MATCH (p)<-[:CONTAINS]-(o:Order)-[:CONTAINS]->(q:Product)
  WHERE o.status <> 'cancelled' AND q <> p
  WITH p, q, count(DISTINCT o) AS communs
  WHERE communs >= 2
  WITH p, q, communs, communs / sqrt(toFloat(p.orders) * q.orders) AS score
  ORDER BY score DESC, communs DESC
  LIMIT 20
  CREATE (p)-[:BOUGHT_WITH {orders: communs, score: round(score, 4)}]->(q)
} IN TRANSACTIONS OF 500 ROWS;
