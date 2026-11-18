--[[ ===========================================================================
Lot 2 — Données de démonstration : sessions et paniers réalistes
-------------------------------------------------------------------------------
Exécution : redis-cli --eval /lot2/init/donnees-demo.lua , <sessions> <invités> <clients>
  - Paniers composés de vrais SKU en vente (réplique du catalogue du Lot 1).
  - Identifiants clients au format du Lot 1 (CUST-000000).
  - Durées de vie restantes tirées au hasard : les données expirent
    progressivement, comme en production.
  - Déterministe (graine fixe) : relancer le script recrée les mêmes clés.
Ces identifiants ne sont PAS cryptographiques : en production, l'identifiant de
session est un jeton aléatoire de 128 bits généré par le serveur web.
=========================================================================== ]]
local nb_sessions, nb_invites, nb_clients = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local TTL_SESSION, TTL_INVITE, TTL_CLIENT = 1800, 604800, 2592000

math.randomseed(20261117)
local skus = redis.call('SMEMBERS', 'catalog:sellable')
table.sort(skus) -- SMEMBERS n'a pas d'ordre garanti : on trie pour rester déterministe
local function hex(n)
  local t = {}
  for i = 1, n do t[i] = string.format('%x', math.random(0, 15)) end
  return table.concat(t)
end
local maintenant = tonumber(redis.call('TIME')[1]) * 1000

-- Un panier : 1 à 4 lignes le plus souvent, parfois 5 à 12 (montage d'une configuration complète)
local function remplir_panier(cle, ttl_max)
  redis.call('DEL', cle)
  local lignes = math.random(1, 4)
  if math.random() < 0.1 then lignes = math.random(5, 12) end
  for _ = 1, lignes do
    redis.call('HSET', cle, skus[math.random(#skus)], math.random(1, 2))
  end
  redis.call('EXPIRE', cle, math.random(60, ttl_max))
end

local clients = {}
for _ = 1, nb_clients do
  local client = string.format('CUST-%06d', math.random(1, 25000))
  clients[#clients + 1] = client
  remplir_panier('cart:customer:' .. client, TTL_CLIENT)
end

local invites = {}
for _ = 1, nb_invites do
  local id = hex(24)
  invites[#invites + 1] = id
  remplir_panier('cart:guest:' .. id, TTL_INVITE)
end

for _ = 1, nb_sessions do
  local id = hex(32)
  local cle = 'session:' .. id
  local creee = maintenant - math.random(0, 3600) * 1000
  local champs = {
    'createdAt', creee,
    'lastSeen', creee + math.random(0, 1800) * 1000,
    'locale', 'fr-FR',
    'device', (math.random() < 0.55) and 'mobile' or 'desktop',
  }
  if math.random() < 0.3 then -- 30 % des visiteurs sont connectés
    local client = clients[math.random(#clients)]
    local index = 'customer:' .. client .. ':sessions'
    for _, v in ipairs({ 'customerId', client, 'cartKey', 'cart:customer:' .. client }) do champs[#champs + 1] = v end
    redis.call('ZADD', index, creee, id)
    redis.call('EXPIRE', index, TTL_CLIENT)
  else
    for _, v in ipairs({ 'cartKey', 'cart:guest:' .. invites[math.random(#invites)] }) do champs[#champs + 1] = v end
  end
  redis.call('DEL', cle)
  redis.call('HSET', cle, unpack(champs))
  redis.call('EXPIRE', cle, math.random(60, TTL_SESSION))
end

return { nb_sessions, nb_invites, nb_clients }
