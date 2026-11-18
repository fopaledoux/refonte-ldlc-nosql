#!/bin/sh
# =============================================================================
# Lot 4 — Requêtes types : recommandations et compatibilité (R1 à R11)
#   docker compose exec neo4j sh /lot4/queries/01-recommandations.sh
# Lecture seule : rejouable à volonté.
# =============================================================================
. /lot4/lib/commun.sh

CPU='CPU-000054'          # AMD Ryzen 9 9900X3D (socket AM5), le processeur le plus commandé
BOITIER='CASE-000724'     # Corsair 6500D Airflow
GPU='GPU-000009'          # Asus ROG Astral OC (GeForce RTX 5090)
CLIENT='CUST-000042'      # client des démonstrations des Lots 2 et 3

titre "R1. Souvent achetés ensemble (fiche produit)" \
      "arêtes précalculées BOUGHT_WITH : 20 au plus par produit, quelle que soit sa popularité"
q "{sku: '$CPU'}" "
MATCH (p:Product {sku: \$sku})-[r:BOUGHT_WITH]->(q:Product)
RETURN q.sku AS sku, q.name AS nom, q.category AS categorie, r.orders AS paniers_communs, r.score AS score
ORDER BY r.score DESC LIMIT 6"
note "un processeur part avec sa carte mère, sa mémoire, son boîtier : le signal vient des paniers du Lot 3"

titre "R2. « Les clients qui ont acheté ce processeur ont aussi acheté cette carte mère »" \
      "calculé à la volée : produit <- commande -> carte mère, puis vérification de compatibilité"
q "{sku: '$CPU'}" "
MATCH (cpu:CPU {sku: \$sku})<-[:CONTAINS]-(o:Order)-[:CONTAINS]->(carte:Motherboard)
WHERE o.status <> 'cancelled'
WITH cpu, carte, count(DISTINCT o) AS paniers
RETURN carte.sku AS sku, carte.name AS carte_mere, paniers,
       EXISTS { (cpu)-[:REQUIRES]->(:Socket)<-[:PROVIDES]-(carte) } AS meme_socket
ORDER BY paniers DESC LIMIT 5"

titre "R3. Produits compatibles avec ce boîtier" \
      "boîtier -PROVIDES-> format <-REQUIRES- carte mère, puis carte mère -PROVIDES-> socket <-REQUIRES- processeur"
q "{sku: '$BOITIER'}" "
MATCH (boitier:Case {sku: \$sku})-[:PROVIDES]->(f:FormFactor)<-[:REQUIRES]-(carte:Motherboard)
RETURN f.name AS format, count(carte) AS cartes_meres,
       [c IN collect(carte) WHERE c.orders > 0 | c.name][..3] AS exemples
ORDER BY cartes_meres DESC"
q "{sku: '$BOITIER'}" "
MATCH (boitier:Case {sku: \$sku})-[:PROVIDES]->(:FormFactor)<-[:REQUIRES]-(carte:Motherboard)
      -[:PROVIDES]->(s:Socket)<-[:REQUIRES]-(cpu:CPU)
RETURN s.name AS socket, count(DISTINCT carte) AS cartes_meres, count(DISTINCT cpu) AS processeurs
ORDER BY processeurs DESC LIMIT 5"
note "deux sauts d'interface : le boîtier mène aux processeurs utilisables, sans table de jointure"

titre "R4. Une configuration complète autour d'un processeur" \
      "3 interfaces (socket, mémoire, format) ; chaque pièce choisie par les paniers, puis par la popularité"
q "{sku: '$CPU'}" "
MATCH (cpu:CPU {sku: \$sku})-[:REQUIRES]->(:Socket)<-[:PROVIDES]-(carte:Motherboard)
OPTIONAL MATCH (cpu)-[bc:BOUGHT_WITH]->(carte)
WITH cpu, carte ORDER BY coalesce(bc.score, 0) DESC, carte.orders DESC LIMIT 3
CALL {
  WITH carte
  MATCH (carte)-[:PROVIDES]->(:MemoryType)<-[:REQUIRES]-(ram:Memory)
  OPTIONAL MATCH (carte)-[b:BOUGHT_WITH]->(ram)
  RETURN ram ORDER BY coalesce(b.score, 0) DESC, ram.orders DESC LIMIT 1
}
CALL {
  WITH carte
  MATCH (carte)-[:REQUIRES]->(:FormFactor)<-[:PROVIDES]-(boitier:Case)
  OPTIONAL MATCH (carte)-[b:BOUGHT_WITH]->(boitier)
  RETURN boitier ORDER BY coalesce(b.score, 0) DESC, boitier.orders DESC LIMIT 1
}
RETURN carte.name AS carte_mere, ram.name AS memoire, boitier.name AS boitier,
       (cpu.price_cents + carte.price_cents + ram.price_cents + boitier.price_cents) / 100.0 AS total_usd"

titre "R5. Compléter un panier (Lot 2) : ce qui manque, compatible avec TOUT le panier" \
      "règle générique : chaque interface exigée est fournie par une autre pièce"
PANIER="['$CPU', 'MB-000227']"
q "{panier: $PANIER}" "
MATCH (p:Product) WHERE p.sku IN \$panier
WITH collect(p) AS panier
UNWIND panier AS p
MATCH (p)-[r:BOUGHT_WITH]->(reco:Product)
WHERE NOT reco IN panier AND NOT reco.category IN [x IN panier | x.category]
WITH panier, reco, sum(r.score) AS score
WHERE all(i IN [(reco)-[:REQUIRES]->(j) | j] WHERE
          any(x IN panier WHERE (x)-[:PROVIDES]->(i))
          OR none(x IN panier WHERE EXISTS { (x)-[:PROVIDES]->(:Interface {kind: i.kind}) }))
  AND all(x IN panier WHERE all(i IN [(x)-[:REQUIRES]->(j) | j] WHERE
          NOT EXISTS { (reco)-[:PROVIDES]->(:Interface {kind: i.kind}) } OR (reco)-[:PROVIDES]->(i)))
RETURN reco.category AS categorie, reco.sku AS sku, reco.name AS nom, round(score, 3) AS score
ORDER BY score DESC LIMIT 6"
note "panier : $CPU (AM5) + MB-000227 (AM5, DDR5, Mini ITX). La mémoire proposée est en DDR5, le boîtier accepte le Mini ITX"

titre "R6. Recommandations personnalisées pour un client" \
      "filtrage collaboratif par produit : ce que j'ai acheté -BOUGHT_WITH-> ce que je n'ai pas encore"
q "{client: '$CLIENT'}" "
MATCH (moi:Customer {id: \$client})-[:PLACED]->(:Order)-[:CONTAINS]->(achat:Product)
WITH moi, collect(DISTINCT achat) AS achats
UNWIND achats AS p
MATCH (p)-[r:BOUGHT_WITH]->(reco:Product)
WHERE NOT reco IN achats
WITH reco, sum(r.score) AS score, collect(DISTINCT p.name)[..2] AS parce_que_vous_avez_achete
RETURN reco.sku AS sku, reco.name AS nom, reco.category AS categorie, round(score, 3) AS score,
       parce_que_vous_avez_achete
ORDER BY score DESC LIMIT 5"

titre "R7. « Amis d'amis » : les clients qui ont acheté comme moi, et ce qu'ils ont acheté d'autre" \
      "filtrage collaboratif par client, à la volée : 4 sauts, freiné par les produits trop populaires"
q "{client: '$CLIENT'}" "
MATCH (moi:Customer {id: \$client})-[:PLACED]->(:Order)-[:CONTAINS]->(commun:Product)
WITH moi, collect(DISTINCT commun) AS mes_achats
UNWIND mes_achats AS commun
MATCH (commun)<-[:CONTAINS]-(:Order)<-[:PLACED]-(voisin:Customer)
WHERE voisin <> moi
WITH moi, mes_achats, voisin, count(DISTINCT commun) AS en_commun
ORDER BY en_commun DESC LIMIT 25
MATCH (voisin)-[:PLACED]->(:Order)-[:CONTAINS]->(reco:Product)
WHERE NOT reco IN mes_achats
WITH reco, sum(en_commun / log(2.0 + reco.orders)) AS score, count(DISTINCT voisin) AS voisins
RETURN reco.sku AS sku, reco.name AS nom, reco.category AS categorie, voisins, round(score, 2) AS score
ORDER BY score DESC LIMIT 5"
note "25 voisins au plus, et un poids 1/log(popularité) : un best-seller ne doit pas remonter pour tout le monde"

titre "R8. Vu sans être acheté, et des alternatives proches" \
      "VIEWED (Lot 3) ; même puce graphique, ou même catégorie et mêmes interfaces, prix à ±20 %"
q "" "
MATCH (c:Customer)-[v:VIEWED]->(vu:Product)
WHERE NOT EXISTS { (c)-[:PLACED]->(:Order)-[:CONTAINS]->(vu) }
WITH c, vu, v ORDER BY v.count DESC, v.last_at DESC LIMIT 1
MATCH (vu)-[:IN_CATEGORY]->(cat:Category)<-[:IN_CATEGORY]-(alt:Product)
WHERE alt <> vu AND abs(alt.price_cents - vu.price_cents) <= vu.price_cents * 0.2
  AND all(i IN [(vu)-[:REQUIRES|PROVIDES|USES_CHIP]->(j) | j] WHERE (alt)-[:REQUIRES|PROVIDES|USES_CHIP]->(i))
RETURN c.id AS client, vu.name AS vu_sans_achat, v.count AS vues, alt.name AS alternative,
       alt.price_cents / 100.0 AS prix_usd, alt.orders AS commandes
ORDER BY commandes DESC LIMIT 5"

titre "R9. Alternatives à une carte graphique (même puce, autres marques)" \
      "produit -USES_CHIP-> puce <-USES_CHIP- alternatives"
q "{sku: '$GPU'}" "
MATCH (gpu:GPU {sku: \$sku})-[:USES_CHIP]->(puce:Chipset)<-[:USES_CHIP]-(alt:GPU)
WHERE alt <> gpu
RETURN puce.name AS puce, alt.brand AS marque, alt.name AS nom, alt.price_cents / 100.0 AS prix_usd, alt.orders AS commandes
ORDER BY commandes DESC, prix_usd LIMIT 5"

titre "R10. Même gamme, autre catégorie (écosystème)" \
      "produit -IN_SERIES-> gamme <-IN_SERIES- produits d'autres catégories"
q "{sku: '$GPU'}" "
MATCH (gpu:Product {sku: \$sku})-[:IN_SERIES]->(gamme:Series)<-[:IN_SERIES]-(autre:Product)
WHERE autre.category <> gpu.category
WITH gamme, autre ORDER BY autre.orders DESC
RETURN gamme.id AS gamme, autre.category AS categorie, collect(autre.name)[..2] AS produits, count(*) AS en_vente
ORDER BY en_vente DESC LIMIT 5"

titre "R11. Pourquoi ce produit est-il recommandé ? Le chemin, en clair" \
      "un graphe explique ses recommandations : on renvoie le chemin lui-même"
q "{client: '$CLIENT'}" "
MATCH (moi:Customer {id: \$client})-[:PLACED]->(o:Order)-[:CONTAINS]->(p:Product)-[r:BOUGHT_WITH]->(reco:Product)
WHERE NOT EXISTS { (moi)-[:PLACED]->(:Order)-[:CONTAINS]->(reco) }
WITH moi, o, p, r, reco ORDER BY r.score DESC LIMIT 1
RETURN moi.id + ' a commandé ' + p.name + ' (' + o.id + ') ; ' + r.orders + ' paniers contiennent aussi '
       + reco.name AS explication"
