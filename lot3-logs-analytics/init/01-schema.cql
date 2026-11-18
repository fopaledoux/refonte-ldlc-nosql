-- =============================================================================
-- Lot 3 — Logs et analytics : keyspace et tables (Cassandra 5.0)
--
-- Modélisation Query-Driven : une table par requête métier, données dupliquées
-- volontairement. Chaque table indique la requête qu'elle sert (Q1 à Q9, voir
-- docs/conception.md §4) et la taille de ses partitions.
--
-- Rappels de vocabulaire :
--   PRIMARY KEY ((clé de partition), colonnes de clustering)
--   - la clé de partition choisit le nœud et regroupe les lignes lues ensemble ;
--   - les colonnes de clustering trient les lignes DANS la partition, sur disque.
-- =============================================================================

-- Développement : un nœud, une copie. Production : 3 nœuds sur 3 racks (zones),
-- {'dc1': 3}, et éventuellement un second datacenter dédié aux jobs analytiques.
CREATE KEYSPACE IF NOT EXISTS ldlc_analytics
  WITH replication = {'class': 'NetworkTopologyStrategy', 'dc1': 1}
  AND durable_writes = true;

USE ldlc_analytics;

-- -----------------------------------------------------------------------------
-- Q6. Événements bruts d'une heure (jobs de recalcul, audit, export vers le
--     stockage froid). C'est le journal : tout clic y est écrit une fois.
-- Partition = (jour, heure, bucket). Sans le bucket, toutes les écritures d'une
-- heure iraient sur les mêmes 3 réplicas, et une heure de pointe un jour de
-- sortie (environ 1,5 million d'événements) ferait une partition de 176 Mo.
-- Avec 16 buckets (identifiant d'événement modulo 16) : environ 94 000 lignes
-- et 11 Mo au pire (117 octets par ligne, mesuré), réparties sur tout le cluster.
-- Un job lit une heure en 16 requêtes ciblées, sans balayer la table.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events_by_hour (
  day          date,
  hour         tinyint,
  bucket       tinyint,
  ts           timestamp,
  event_id     uuid,          -- fourni par le collecteur : rejouer un événement réécrit la même ligne
  type         text,          -- page_view, product_view, search, click, cart_add, cart_remove
  visit_id     uuid,          -- identifiant de visite, distinct du jeton de session du Lot 2
  customer_id  text,          -- CUST-000000 si le visiteur est connecté
  sku          text,
  page         text,
  search_query text,
  results      int,           -- nombre de résultats d'une recherche
  target       text,          -- élément cliqué (add_to_cart, compare, filter...)
  quantity     int,           -- événements du panier
  device       text,
  PRIMARY KEY ((day, hour, bucket), ts, event_id)
) WITH CLUSTERING ORDER BY (ts ASC, event_id ASC)
  -- 90 jours, puis les données partent en stockage froid (Parquet) si besoin
  AND default_time_to_live = 7776000
  -- Fenêtres d'un jour : un SSTable par jour, supprimé d'un bloc quand tout a expiré
  AND compaction = {'class': 'TimeWindowCompactionStrategy',
                    'compaction_window_unit': 'DAYS', 'compaction_window_size': 1}
  -- Données jamais supprimées, seulement expirées : l'expiration est identique sur
  -- chaque réplica, inutile de garder les tombstones 10 jours
  AND gc_grace_seconds = 86400
  AND comment = 'Q6 : journal brut, une partition par heure et par bucket';

-- -----------------------------------------------------------------------------
-- Q5. Parcours d'une visite, dans l'ordre (support client, analyse UX).
-- Partition = visite : quelques dizaines de lignes au plus, aucun découpage requis.
-- device est une colonne STATIC : stockée une seule fois par partition.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events_by_visit (
  visit_id     uuid,
  ts           timestamp,
  event_id     uuid,
  device       text STATIC,
  type         text,
  customer_id  text,
  sku          text,
  page         text,
  search_query text,
  results      int,
  target       text,
  PRIMARY KEY ((visit_id), ts, event_id)
) WITH CLUSTERING ORDER BY (ts ASC, event_id ASC)
  AND default_time_to_live = 7776000
  AND compaction = {'class': 'TimeWindowCompactionStrategy',
                    'compaction_window_unit': 'DAYS', 'compaction_window_size': 1}
  AND gc_grace_seconds = 86400
  AND comment = 'Q5 : parcours d''une visite';

-- -----------------------------------------------------------------------------
-- Q1. « Combien de vues pour ce produit aujourd'hui ? » et Q7 : courbe des
--     30 derniers jours (même partition, tranche de lignes).
-- Partition = SKU, une ligne par jour : 365 lignes par an, quelques Ko.
-- Compteurs : incrémentés par le service d'ingestion, qui agrège ses événements
-- toutes les quelques secondes (un incrément de +734 au lieu de 734 incréments :
-- c'est ce qui protège la partition d'une carte graphique le jour de sa sortie).
-- Un compteur n'accepte pas de TTL : purge mensuelle des jours de plus de 25 mois.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_daily_counts (
  sku        text,
  day        date,
  views      counter,
  cart_adds  counter,
  PRIMARY KEY ((sku), day)
) WITH CLUSTERING ORDER BY (day DESC)
  AND comment = 'Q1, Q7 : vues et ajouts au panier par produit et par jour';

-- -----------------------------------------------------------------------------
-- Entrée du job de classement (Q2). Les mêmes vues, rangées par jour.
-- Partition = (jour, shard) avec shard = hash(SKU) modulo 16 : une partition
-- « jour » unique recevrait toutes les écritures du site. Le job lit une semaine
-- en 7 x 16 = 112 requêtes ciblées (environ 1 000 lignes chacune).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_views_by_day (
  day    date,
  shard  tinyint,
  sku    text,
  views  counter,
  PRIMARY KEY ((day, shard), sku)
) WITH comment = 'Entrée du job de classement : vues du jour, réparties en 16 shards';

-- -----------------------------------------------------------------------------
-- Q2. « Top produits consultés cette semaine » (global et par catégorie), top
--     du jour, et Q9 : recherches les plus fréquentes et recherches sans résultat.
-- CQL ne trie pas sur un agrégat : le classement est précalculé par un job
-- (chaque heure) et la lecture est une seule partition, déjà triée par rang.
-- period : 'day:2026-11-18' ou 'week:2026-W47' ;
-- ranking : 'products:all', 'products:<catégorie>', 'searches:all', 'searches:no-result'.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rankings (
  period       text,
  ranking      text,
  rank         int,
  item         text,          -- SKU ou terme de recherche
  label        text,          -- nom du produit, recopié au moment du calcul
  category     text,
  value        bigint,        -- vues ou nombre de recherches
  computed_at  timestamp,
  PRIMARY KEY ((period, ranking), rank)
) WITH CLUSTERING ORDER BY (rank ASC)
  -- 25 mois : durée de conservation maximale recommandée par la CNIL pour la mesure d'audience
  AND default_time_to_live = 65664000
  AND comment = 'Q2, Q9 : classements précalculés par le job';

-- -----------------------------------------------------------------------------
-- Q3. « Historique des commandes d'un client », la plus récente d'abord, paginé.
-- Partition = client : quelques dizaines de commandes, quelques Mo pour le plus
-- gros compte professionnel sur 10 ans. Les lignes sont recopiées (nom, prix
-- payé) : la page s'affiche en une lecture, sans jointure.
-- Cassandra n'est PAS la source de vérité des commandes (paiement, stock :
-- transactions ACID dans le système de gestion des commandes) ; elle en garde
-- l'historique, alimenté par les événements de commande.
-- -----------------------------------------------------------------------------
CREATE TYPE IF NOT EXISTS order_line (
  sku         text,
  name        text,
  quantity    int,
  unit_price  decimal
);

CREATE TABLE IF NOT EXISTS orders_by_customer (
  customer_id  text,
  ordered_at   timestamp,
  order_id     text,
  status       text,          -- paid, shipped, delivered, cancelled, returned
  total        decimal,
  currency     text,
  item_count   int,
  lines        frozen<list<frozen<order_line>>>,
  PRIMARY KEY ((customer_id), ordered_at, order_id)
) WITH CLUSTERING ORDER BY (ordered_at DESC, order_id ASC)
  AND comment = 'Q3 : historique des commandes d''un client';

-- -----------------------------------------------------------------------------
-- Q4. Une commande par son numéro (service client, mise à jour du statut).
-- Partition = commande. Sert aussi à retrouver customer_id et ordered_at, sans
-- lesquels on ne peut pas mettre à jour la ligne de orders_by_customer.
-- Les deux tables sont écrites ensemble dans un BATCH journalisé (LOGGED).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders_by_id (
  order_id        text,
  customer_id     text,
  ordered_at      timestamp,
  status          text,
  status_history  map<timestamp, text>,
  total           decimal,
  currency        text,
  lines           frozen<list<frozen<order_line>>>,
  PRIMARY KEY ((order_id))
) WITH comment = 'Q4 : commande par numéro';

-- -----------------------------------------------------------------------------
-- Q8. Historique des prix d'un produit et prix le plus bas des 30 jours qui
--     précèdent une promotion (obligation légale : directive Omnibus, article
--     L112-1-1 du Code de la consommation). Le Lot 1 ne garde que le prix courant.
-- Partition = SKU, une ligne par changement de prix (quelques dizaines par an).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_history_by_product (
  sku            text,
  changed_at     timestamp,
  price          decimal,
  regular_price  decimal,     -- prix hors promotion, renseigné pendant une promotion
  currency       text,
  reason         text,        -- initial, update, promo, promo_end
  PRIMARY KEY ((sku), changed_at)
) WITH CLUSTERING ORDER BY (changed_at DESC)
  AND comment = 'Q8 : historique des prix (alimenté par les Change Streams du Lot 1)';
