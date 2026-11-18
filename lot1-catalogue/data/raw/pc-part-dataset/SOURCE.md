# Source des données du catalogue

| | |
|---|---|
| Jeu de données | **PC Part Dataset** |
| Dépôt | https://github.com/docyx/pc-part-dataset |
| Fichier récupéré | `data/json.zip` (926 Ko), extrait dans `json/` (25 fichiers, 10,3 Mo) |
| Version | mise à jour du 23 juillet 2025 |
| Licence | MIT, Copyright (c) 2021-2025 Doc Oliver (voir [`LICENSE`](LICENSE)) |
| Contenu | 66 778 produits issus de PCPartPicker, répartis en 25 catégories |

## Ce qui est utilisé tel quel

- Nom du produit, prix (en **USD**, conservé tel quel), attributs techniques propres à chaque catégorie.

## Ce qui est déduit par nos scripts (et signalé comme tel)

- **SKU** : `PREFIXE-000001`, attribué dans l'ordre du fichier source (`lot1-catalogue/lib/catalogue-config.js`).
- **Marque** : extraite du début du nom (liste explicite des marques en plusieurs mots).
- **Statut** : `active` si le produit a un prix, `discontinued` sinon (PCPartPicker garde les produits qui ne sont plus vendus).
- **Socket des processeurs** : déduit de la microarchitecture, uniquement pour les architectures récentes sans ambiguïté (attribut marqué `derived` dans la catégorie).
- **Formats de cartes mères acceptés par un boîtier** : déduits du type de boîtier.

## Ce qui est synthétique

- **Les avis clients** : le jeu ne contient pas d'avis. Ils sont générés de façon déterministe par
  `lot1-catalogue/init/03-avis-synthetiques.js` et portent le champ `synthetic: true`.

## Récupérer à nouveau les données

```bash
curl -L -o json.zip https://raw.githubusercontent.com/docyx/pc-part-dataset/main/data/json.zip
python -m zipfile -e json.zip .
```
