#!/bin/sh
# =============================================================================
# Lot 4 — Mesures : taille du graphe, nœuds intermédiaires contre arêtes directes,
# supernœuds, coût des parcours (PROFILE : accès à la base et temps).
#   docker compose exec neo4j sh /lot4/queries/02-mesures.sh
# =============================================================================
. /lot4/lib/commun.sh

titre "M1. Taille du graphe"
q "" "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS noeuds ORDER BY noeuds DESC"
q "" "MATCH ()-[r]->() RETURN type(r) AS arete, count(*) AS nombre ORDER BY nombre DESC"
echo "   Sur disque : $(du -sh /data/databases/neo4j | cut -f1) (base, au dernier point de contrôle : toutes les 15 min), $(du -sh /data/transactions/neo4j | cut -f1) (journal de transactions)"

titre "M2. Compatibilité : nœuds d'interface ou arêtes directes produit -> produit ?" \
      "même information, deux modèles : on compte les arêtes de chacun"
q "" "
MATCH ()-[r:REQUIRES|PROVIDES]->(:Interface) WITH count(r) AS via_interfaces
MATCH (a:Product)-[:REQUIRES]->(i:Interface)<-[:PROVIDES]-(b:Product)
RETURN via_interfaces, count(*) AS aretes_directes, count(*) / via_interfaces AS rapport"
q "" "
MATCH (a:Product)-[:REQUIRES]->(i:Interface)<-[:PROVIDES]-(b:Product)
RETURN i.kind AS interface, count(*) AS paires_compatibles ORDER BY paires_compatibles DESC"
note "une carte mère AM5 de plus : 3 arêtes (socket, mémoire, format) au lieu d'une par processeur, barrette et boîtier compatibles"

titre "M3. Supernœuds : les nœuds les plus connectés" \
      "les parcours ne doivent jamais les traverser en entier"
q "" "
MATCH (n) WHERE n:Interface OR n:Brand OR n:Category OR n:Chipset
WITH n, COUNT { (n)--() } AS degre
RETURN labels(n)[0] AS type, coalesce(n.name, n.id) AS noeud, degre ORDER BY degre DESC LIMIT 6"
q "" "
MATCH (p:Product) RETURN p.sku AS sku, p.name AS produit, p.orders AS commandes,
       COUNT { (p)-[:BOUGHT_WITH]->() } AS aretes_bought_with ORDER BY commandes DESC LIMIT 3"

PLUS_VENDU=$($CS --format plain "MATCH (p:Product) RETURN p.sku ORDER BY p.orders DESC LIMIT 1" | tail -n 1 | tr -d '"')

titre "M4. « Achetés ensemble » : à la volée ou précalculé ?" \
      "sur le produit le plus vendu ($PLUS_VENDU), cache chaud"
echo "   À la volée (produit <- commandes -> produits) :"
profil "{sku: '$PLUS_VENDU'}" "
MATCH (p:Product {sku: \$sku})<-[:CONTAINS]-(o:Order)-[:CONTAINS]->(q:Product)
WHERE o.status <> 'cancelled' AND q <> p
RETURN q.sku, count(DISTINCT o) AS n ORDER BY n DESC LIMIT 5"
echo "   Précalculé (BOUGHT_WITH, 20 arêtes au plus) :"
profil "{sku: '$PLUS_VENDU'}" "
MATCH (p:Product {sku: \$sku})-[r:BOUGHT_WITH]->(q:Product)
RETURN q.sku, r.orders ORDER BY r.score DESC LIMIT 5"

titre "M5. Recommandations personnalisées : par produit (R6) ou par client (R7) ?" \
      "client CUST-000042, cache chaud"
echo "   R6, par produit, arêtes précalculées :"
profil "{client: 'CUST-000042'}" "
MATCH (moi:Customer {id: \$client})-[:PLACED]->(:Order)-[:CONTAINS]->(achat:Product)
WITH collect(DISTINCT achat) AS achats UNWIND achats AS p
MATCH (p)-[r:BOUGHT_WITH]->(reco:Product) WHERE NOT reco IN achats
RETURN reco.sku, sum(r.score) AS s ORDER BY s DESC LIMIT 5"
echo "   R7, par client, à la volée (4 sauts) :"
profil "{client: 'CUST-000042'}" "
MATCH (moi:Customer {id: \$client})-[:PLACED]->(:Order)-[:CONTAINS]->(commun:Product)
WITH moi, collect(DISTINCT commun) AS mes_achats UNWIND mes_achats AS commun
MATCH (commun)<-[:CONTAINS]-(:Order)<-[:PLACED]-(voisin:Customer) WHERE voisin <> moi
WITH moi, mes_achats, voisin, count(DISTINCT commun) AS en_commun ORDER BY en_commun DESC LIMIT 25
MATCH (voisin)-[:PLACED]->(:Order)-[:CONTAINS]->(reco:Product) WHERE NOT reco IN mes_achats
RETURN reco.sku, sum(en_commun / log(2.0 + reco.orders)) AS s ORDER BY s DESC LIMIT 5"

titre "M6. Compatibilité : passer par un supernœud ou par une interface ciblée ?" \
      "barrettes compatibles avec une carte mère DDR5, les 5 plus vendues"
CARTE=$($CS --format plain "MATCH (m:Motherboard)-[:PROVIDES]->(:MemoryType {name: 'DDR5'}) RETURN m.sku ORDER BY m.orders DESC LIMIT 1" | tail -n 1 | tr -d '"')
echo "   Par la catégorie (toutes les barrettes, filtre sur le type ensuite) :"
profil "{sku: '$CARTE'}" "
MATCH (m:Motherboard {sku: \$sku})-[:PROVIDES]->(t:MemoryType)
MATCH (:Category {id: 'memoire-ram'})<-[:IN_CATEGORY]-(ram:Product)
WHERE (ram)-[:REQUIRES]->(t)
RETURN ram.sku ORDER BY ram.orders DESC LIMIT 5"
echo "   Par l'interface DDR5 (seulement les barrettes compatibles) :"
profil "{sku: '$CARTE'}" "
MATCH (m:Motherboard {sku: \$sku})-[:PROVIDES]->(:MemoryType)<-[:REQUIRES]-(ram:Memory)
RETURN ram.sku ORDER BY ram.orders DESC LIMIT 5"
