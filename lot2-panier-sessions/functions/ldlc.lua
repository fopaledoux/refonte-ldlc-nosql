#!lua name=ldlc
--[[ ===========================================================================
Lot 2 — Bibliothèque de fonctions Redis « ldlc » : panier et sessions
-------------------------------------------------------------------------------
Chargée une fois (FUNCTION LOAD), stockée et répliquée par Redis, appelée par
les serveurs web avec FCALL. Chaque fonction s'exécute de façon ATOMIQUE :
aucune autre commande ne s'intercale. Les contrôles, la modification, le
rafraîchissement du TTL et l'émission de l'événement forment un seul tout.

Toutes les clés manipulées sont passées dans KEYS (compatible Redis Cluster
si les clés d'un même appel partagent un hash tag).

Clés utilisées (voir docs/conception.md, Lot 2) :
  session:{id}                Hash   données de session            TTL glissant 30 min
  cart:guest:{id}             Hash   SKU -> quantité (invité)       TTL glissant 7 jours
  cart:customer:{CUST-…}      Hash   SKU -> quantité (client)       TTL glissant 30 jours
  customer:{CUST-…}:sessions  ZSet   sessions du client (score = date de connexion)
  catalog:sellable            Set    SKU en vente (réplique du Lot 1)
  catalog:limits              Hash   SKU -> quantité max (sorties de composants)
  events:cart                 Stream événements du panier (outbox vers le Lot 3)
=========================================================================== ]]

local LONGUEUR_FLUX = 100000 -- taille approximative conservée dans events:cart

local function maintenant_ms()
  local t = redis.call('TIME')
  return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end

local function entier(v)
  local n = tonumber(v)
  if n and n == math.floor(n) then return n end
  return nil
end

-- Outbox : l'événement est écrit dans le même appel atomique que la modification
-- du panier. Il ne peut donc ni manquer ni exister sans la modification.
local function publier(flux, ...)
  redis.call('XADD', flux, 'MAXLEN', '~', LONGUEUR_FLUX, '*', 'ts', maintenant_ms(), ...)
end

local function limite(cle_limites, sku, defaut)
  return entier(redis.call('HGET', cle_limites, sku)) or defaut
end

-- ---------------------------------------------------------------------------
-- cart_add : ajoute une quantité d'un produit au panier
--   KEYS : panier, catalog:sellable, catalog:limits, events:cart
--   ARGV : sku, quantité à ajouter, ttl (s), limite par défaut
--   Renvoie : { quantité dans le panier, limite appliquée, quantité réellement ajoutée }
-- ---------------------------------------------------------------------------
local function cart_add(keys, args)
  local panier, vendables, limites, flux = keys[1], keys[2], keys[3], keys[4]
  local sku, qte, ttl, defaut = args[1], entier(args[2]), entier(args[3]), entier(args[4]) or 10
  if not qte or qte < 1 then return redis.error_reply('BADQTY quantité invalide') end
  if not ttl or ttl < 1 then return redis.error_reply('BADTTL durée de vie invalide') end
  if redis.call('SISMEMBER', vendables, sku) == 0 then
    return redis.error_reply('NOTSELLABLE produit non vendable : ' .. sku)
  end
  local max = limite(limites, sku, defaut)
  local actuelle = entier(redis.call('HGET', panier, sku)) or 0
  local nouvelle = math.min(actuelle + qte, max)
  if nouvelle ~= actuelle then
    redis.call('HSET', panier, sku, nouvelle)
    publier(flux, 'type', 'add', 'cart', panier, 'sku', sku, 'qty', nouvelle - actuelle)
  end
  redis.call('EXPIRE', panier, ttl) -- toute action prolonge la vie du panier
  return { nouvelle, max, nouvelle - actuelle }
end

-- ---------------------------------------------------------------------------
-- cart_set : fixe la quantité d'une ligne existante (0 = supprimer la ligne)
--   KEYS : panier, catalog:limits, events:cart
--   ARGV : sku, quantité, ttl (s), limite par défaut
--   Renvoie : { quantité dans le panier, limite appliquée }
-- ---------------------------------------------------------------------------
local function cart_set(keys, args)
  local panier, limites, flux = keys[1], keys[2], keys[3]
  local sku, qte, ttl, defaut = args[1], entier(args[2]), entier(args[3]), entier(args[4]) or 10
  if not qte or qte < 0 then return redis.error_reply('BADQTY quantité invalide') end
  if redis.call('HEXISTS', panier, sku) == 0 then
    return redis.error_reply('NOLINE ce produit n\'est pas dans le panier : ' .. sku)
  end
  local max = limite(limites, sku, defaut)
  if qte == 0 then
    redis.call('HDEL', panier, sku)
    publier(flux, 'type', 'remove', 'cart', panier, 'sku', sku)
    if redis.call('HLEN', panier) == 0 then
      redis.call('DEL', panier) -- un panier vide n'occupe pas de mémoire
      return { 0, max }
    end
  else
    qte = math.min(qte, max)
    redis.call('HSET', panier, sku, qte)
    publier(flux, 'type', 'set', 'cart', panier, 'sku', sku, 'qty', qte)
  end
  redis.call('EXPIRE', panier, ttl)
  return { qte, max }
end

-- ---------------------------------------------------------------------------
-- cart_get : lit le panier et prolonge sa durée de vie (consulter = activité)
--   KEYS : panier       ARGV : ttl (s)
--   Renvoie : [sku1, qté1, sku2, qté2, …] ou une liste vide
-- ---------------------------------------------------------------------------
local function cart_get(keys, args)
  if redis.call('EXISTS', keys[1]) == 0 then return {} end
  redis.call('EXPIRE', keys[1], entier(args[1]))
  return redis.call('HGETALL', keys[1])
end

-- ---------------------------------------------------------------------------
-- cart_merge : à la connexion, fusionne le panier invité dans le panier client
--   KEYS : panier invité, panier client, catalog:limits, events:cart
--   ARGV : ttl du panier client (s), limite par défaut
--   Renvoie : nombre de lignes fusionnées
--   Les quantités s'additionnent, dans la limite par produit ; le panier invité
--   est supprimé. Atomique : un double clic sur « connexion » ne fusionne pas deux fois.
-- ---------------------------------------------------------------------------
local function cart_merge(keys, args)
  local invite, client, limites, flux = keys[1], keys[2], keys[3], keys[4]
  local ttl, defaut = entier(args[1]), entier(args[2]) or 10
  local lignes = redis.call('HGETALL', invite)
  for i = 1, #lignes, 2 do
    local sku = lignes[i]
    local qte = entier(lignes[i + 1]) or 0
    local actuelle = entier(redis.call('HGET', client, sku)) or 0
    redis.call('HSET', client, sku, math.min(actuelle + qte, limite(limites, sku, defaut)))
  end
  if #lignes > 0 then
    redis.call('DEL', invite)
    publier(flux, 'type', 'merge', 'cart', client, 'from', invite, 'lines', #lignes / 2)
  end
  if redis.call('EXISTS', client) == 1 then redis.call('EXPIRE', client, ttl) end
  return #lignes / 2
end

-- ---------------------------------------------------------------------------
-- session_create : crée une session (refuse d'écraser une session existante)
--   KEYS : session      ARGV : ttl (s), puis paires champ valeur
--   Renvoie : le contenu de la session
-- ---------------------------------------------------------------------------
local function session_create(keys, args)
  local session, ttl = keys[1], entier(args[1])
  if redis.call('EXISTS', session) == 1 then return redis.error_reply('EXISTS session déjà existante') end
  local maintenant = maintenant_ms()
  redis.call('HSET', session, 'createdAt', maintenant, 'lastSeen', maintenant)
  for i = 2, #args - 1, 2 do redis.call('HSET', session, args[i], args[i + 1]) end
  redis.call('EXPIRE', session, ttl)
  return redis.call('HGETALL', session)
end

-- ---------------------------------------------------------------------------
-- session_touch : appelée à CHAQUE requête HTTP, par n'importe quel serveur web
--   KEYS : session      ARGV : ttl (s)
--   Renvoie : le contenu de la session, ou nil si elle a expiré
-- ---------------------------------------------------------------------------
local function session_touch(keys, args)
  if redis.call('EXISTS', keys[1]) == 0 then return false end
  redis.call('HSET', keys[1], 'lastSeen', maintenant_ms())
  redis.call('EXPIRE', keys[1], entier(args[1]))
  return redis.call('HGETALL', keys[1])
end

-- ---------------------------------------------------------------------------
-- session_login : connexion d'un client
--   KEYS : session actuelle, nouvelle session, customer:{id}:sessions
--   ARGV : identifiant client, nouvel identifiant de session, clé du panier client,
--          ttl de la session (s), ttl de l'index des sessions (s)
--   L'identifiant de session CHANGE à la connexion (RENAME) : un identifiant volé
--   ou imposé avant la connexion devient inutilisable (protection contre la
--   fixation de session).
-- ---------------------------------------------------------------------------
local function session_login(keys, args)
  local ancienne, nouvelle, index = keys[1], keys[2], keys[3]
  local client, id, panier = args[1], args[2], args[3]
  local ttl, ttl_index = entier(args[4]), entier(args[5])
  if redis.call('EXISTS', ancienne) == 0 then return redis.error_reply('NOSESSION session expirée') end
  redis.call('RENAME', ancienne, nouvelle)
  local maintenant = maintenant_ms()
  redis.call('HSET', nouvelle, 'customerId', client, 'cartKey', panier, 'loggedInAt', maintenant, 'lastSeen', maintenant)
  redis.call('EXPIRE', nouvelle, ttl)
  redis.call('ZADD', index, maintenant, id)
  redis.call('EXPIRE', index, ttl_index)
  return redis.call('HGETALL', nouvelle)
end

redis.register_function('cart_add', cart_add)
redis.register_function('cart_set', cart_set)
redis.register_function('cart_get', cart_get)
redis.register_function('cart_merge', cart_merge)
redis.register_function('session_create', session_create)
redis.register_function('session_touch', session_touch)
redis.register_function('session_login', session_login)
