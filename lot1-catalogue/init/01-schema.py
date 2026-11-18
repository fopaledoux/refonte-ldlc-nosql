"""
Lot 1 — 01 : collections et règles de validation ($jsonSchema)

Principe : « flexible mais pas anarchique ».
 - Le validateur impose le TRONC COMMUN de toutes les fiches (identité, prix,
   catégorie, marque, statut) et les BORNES des tableaux embarqués.
 - Il laisse libres les attributs propres à chaque catégorie (specs) et les
   sous-documents spécifiques (ex. `software`) : aucune migration n'est
   nécessaire pour ajouter un attribut ou une catégorie.
 - validationLevel "moderate" : si le validateur est durci plus tard, les
   anciens documents non conformes restent modifiables (migration progressive).
Ce script REMET À ZÉRO les collections du lot 1.
"""
import sys

sys.path.insert(0, "/lot1/lib")
from outils import connecter  # noqa: E402

db = connecter()
print("[01] Création des collections et des validateurs")

for nom in ["products", "reviews", "categories", "brands"]:
    if nom in db.list_collection_names():
        db.drop_collection(nom)

db.create_collection("products", validator={
    "$jsonSchema": {
        "bsonType": "object",
        "title": "Fiche produit",
        "required": ["_id", "schemaVersion", "type", "name", "slug", "brand", "category", "status", "specs", "createdAt"],
        "properties": {
            "_id": {"bsonType": "string", "pattern": "^[A-Z]+-[0-9]{6}$", "description": "SKU : identifiant commun aux 4 lots"},
            "schemaVersion": {"bsonType": "number", "minimum": 1},
            "type": {"enum": ["physical", "software"], "description": "Discriminant du Polymorphic pattern"},
            "name": {"bsonType": "string", "minLength": 1},
            "slug": {"bsonType": "string", "pattern": "^[a-z0-9-]+$"},
            "status": {"enum": ["active", "discontinued", "draft"]},
            "brand": {
                "bsonType": "object", "required": ["id", "name"],
                "properties": {"id": {"bsonType": "string"}, "name": {"bsonType": "string"}},
            },
            "category": {
                "bsonType": "object", "required": ["id", "name", "path"],
                "properties": {
                    "id": {"bsonType": "string"},
                    "name": {"bsonType": "string"},
                    "path": {"bsonType": "array", "minItems": 1, "items": {"bsonType": "string"}},
                },
            },
            "price": {
                "bsonType": ["object", "null"], "required": ["amount", "currency"],
                "properties": {"amount": {"bsonType": "decimal", "minimum": 0}, "currency": {"enum": ["USD", "EUR"]}},
            },
            "specs": {
                "bsonType": "array",
                "items": {"bsonType": "object", "required": ["k", "v"],
                          "properties": {"k": {"bsonType": "string"}, "u": {"bsonType": "string"}}},
            },
            "rating": {
                "bsonType": "object", "required": ["count"],
                "properties": {"count": {"bsonType": "number", "minimum": 0},
                               "average": {"bsonType": ["double", "int", "null"]}},
            },
            "recentReviews": {"bsonType": "array", "maxItems": 5, "description": "Subset pattern : 5 avis au plus"},
            "compatibleProducts": {
                "bsonType": "array", "maxItems": 10, "description": "Liste courte, bornée",
                "items": {"bsonType": "object", "required": ["_id", "name"]},
            },
            "createdAt": {"bsonType": "date"},
            "updatedAt": {"bsonType": "date"},
        },
    },
}, validationLevel="moderate", validationAction="error")

db.create_collection("reviews", validator={
    "$jsonSchema": {
        "bsonType": "object",
        "title": "Avis client",
        "required": ["productId", "customer", "rating", "status", "createdAt"],
        "properties": {
            "productId": {"bsonType": "string", "description": "Référence vers products._id"},
            "customer": {
                "bsonType": "object", "required": ["id", "displayName"],
                "properties": {"id": {"bsonType": "string"}, "displayName": {"bsonType": "string"}},
            },
            "rating": {"bsonType": "number", "minimum": 1, "maximum": 5, "multipleOf": 1},
            "title": {"bsonType": "string", "maxLength": 120},
            "body": {"bsonType": "string", "maxLength": 5000},
            "status": {"enum": ["pending", "approved", "rejected"]},
            "verifiedPurchase": {"bsonType": "bool"},
            "helpfulVotes": {"bsonType": "number", "minimum": 0},
            "synthetic": {"bsonType": "bool"},
            "createdAt": {"bsonType": "date"},
        },
    },
}, validationLevel="strict", validationAction="error")

db.create_collection("categories", validator={
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "name", "path", "level", "specTemplate"],
        "properties": {
            "_id": {"bsonType": "string"},
            "parent": {"bsonType": ["string", "null"]},
            "path": {"bsonType": "array", "items": {"bsonType": "string"}},
            "level": {"bsonType": "number", "minimum": 1},
            "specTemplate": {
                "bsonType": "array",
                "items": {"bsonType": "object", "required": ["k", "label", "filterable"]},
            },
        },
    },
})

db.create_collection("brands", validator={"$jsonSchema": {"bsonType": "object", "required": ["_id", "name"]}})

print("[01] OK : products, reviews, categories, brands")
