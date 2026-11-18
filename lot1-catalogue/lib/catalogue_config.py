"""
Lot 1 — Configuration du catalogue (importée par les scripts de chargement)

1. L'arborescence des catégories (Tree pattern : référence au parent + tableau des
   ancêtres `path`, calculé à l'import).
2. Pour chaque catégorie feuille : le fichier source du jeu public, le préfixe de
   SKU, et le GABARIT D'ATTRIBUTS (Attribute pattern). Chaque attribut a :
     k          clé technique stockée dans le produit (specs.k)
     label      libellé affiché (stocké une seule fois, dans la catégorie)
     unit       unité (stockée dans le produit : specs.u)
     filterable vrai si l'attribut sert de facette de filtrage
     derived    vrai si la valeur est déduite par nos scripts (pas dans la source)
     get(row)   extraction depuis la ligne source. Peut renvoyer :
                  - None         -> attribut absent pour ce produit
                  - une liste    -> attribut multi-valué (une entrée par valeur)
"""
import re
import unicodedata

from outils import texte


# --- Utilitaires de normalisation ------------------------------------------------
def rmin(v):  # [min, max] ou valeur unique
    return v[0] if isinstance(v, list) else v


def rmax(v):
    return v[-1] if isinstance(v, list) else v


def liste(v):
    return [s.strip() for s in v.split(",") if s.strip()] if isinstance(v, str) else v


def defaut(valeur_si_absent):
    return lambda v: valeur_si_absent if v is None else v


def majuscule(s):
    return s[:1].upper() + s[1:]


def vrai(v):
    """Valeur considérée comme renseignée : ni absente, ni fausse, ni zéro, ni texte vide."""
    return v is not None and v is not False and v != 0 and v != ""


def est_nombre(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def slugify(s):
    s = unicodedata.normalize("NFD", str(s))
    s = re.sub("[̀-ͯ]", "", s).lower()          # retire les accents
    return re.sub("^-+|-+$", "", re.sub("[^a-z0-9]+", "-", s))


# Marques dont le nom contient plusieurs mots (sinon : premier mot du nom).
MARQUES_MULTI_MOTS = [
    "Cooler Master", "Western Digital", "Silicon Power", "Lian Li", "be quiet!",
    "Fractal Design", "In Win", "Tripp Lite", "FSP Group", "Creative Labs", "Mad Catz",
    "Gear Head", "Super Flower", "Arctic Silver", "Thermal Grizzly", "Turtle Beach",
    "Monster Digital", "Black Box", "Micro Center",
]


def extraire_marque(nom):
    for b in MARQUES_MULTI_MOTS:
        if nom.lower().startswith(b.lower() + " "):
            return b
    return re.split(r"\s+", nom)[0]


# Socket déduit de la microarchitecture : uniquement les gammes grand public récentes,
# pour lesquelles la correspondance est sans ambiguïté.
# Limite assumée : même socket ne veut pas dire compatible (chipset, BIOS) ;
# la compatibilité fine relève du Lot 4 (graphe).
SOCKET_PAR_ARCHI = {
    "Zen 5": "AM5", "Zen 4": "AM5",
    "Zen 3": "AM4", "Zen 2": "AM4", "Zen+": "AM4", "Zen": "AM4",
    "Arrow Lake": "LGA1851",
    "Raptor Lake Refresh": "LGA1700", "Raptor Lake": "LGA1700", "Alder Lake": "LGA1700",
    "Rocket Lake": "LGA1200", "Comet Lake": "LGA1200",
    "Coffee Lake Refresh": "LGA1151", "Coffee Lake": "LGA1151",
    "Kaby Lake": "LGA1151", "Skylake": "LGA1151",
}


def socket_processeur(r):
    # Exclut les gammes serveur / HEDT (sockets différents pour une même architecture)
    if re.search("Xeon|EPYC|Threadripper|Opteron", r["name"], re.IGNORECASE):
        return None
    if re.search(r"Core i\d-\d{4,5}XE?\b", r["name"]):           # Core X (LGA2066)
        return None
    return SOCKET_PAR_ARCHI.get(r.get("microarchitecture"))


def formats_carte_mere(type_boitier):
    """Formats de cartes mères acceptés selon le type de boîtier (HTPC : variable, non déduit)."""
    t = type_boitier if isinstance(type_boitier, str) else texte(type_boitier)
    if re.match("ATX Full", t):
        return ["EATX", "ATX", "Micro ATX", "Mini ITX"]
    if re.match("ATX", t):
        return ["ATX", "Micro ATX", "Mini ITX"]
    if re.match("MicroATX", t):
        return ["Micro ATX", "Mini ITX"]
    if re.match("Mini ITX", t):
        return ["Mini ITX"]
    return None


def infos_logiciel(nom):
    """Sous-document propre aux logiciels (Polymorphic pattern)."""
    def cherche(motif):
        m = re.search(motif, nom)
        return m.group(1) if m else None
    media = cherche(r"\b(DVD|USB|Download)\b")
    info = {
        "product": cherche(r"(Windows \d+(?:\.\d+)?)") or nom,
        "edition": cherche(r"\b(Home Premium|Home|Pro|Professional|Ultimate|Enterprise|Education)\b"),
        "licenceType": cherche(r"\b(OEM|Retail|Full|Upgrade)\b"),
        "media": {"DVD": "DVD", "USB": "Clé USB", "Download": "Téléchargement"}.get(media),
        "delivery": "digital" if media == "Download" else ("physical" if media else None),
    }
    return {k: v for k, v in info.items() if v is not None}


def _efficacite(r):
    e = r.get("efficiency")
    if not vrai(e):
        return None
    return "80 PLUS" if e == "plus" else "80 PLUS " + majuscule(e)


def _modularite(r):
    m = r.get("modular")
    libelle = {"Full": "Modulaire", "Semi": "Semi-modulaire", "Full / Side": "Modulaire"}.get(m) if isinstance(m, str) else None
    return libelle or ("Non modulaire" if m is False else m)


def _ddr(r):
    s = r.get("speed")
    return "DDR" + texte(s[0]) if isinstance(s, list) else None


def _type_disque(r):
    t = r.get("type")
    return "SSD" if t == "SSD" else ("HDD" if est_nombre(t) else None)


def _format_disque(r):
    f = r.get("form_factor")
    return texte(f) + " pouces" if est_nombre(f) else f


def _resolution(r):
    v = r.get("resolution")
    return "x".join(texte(x) for x in v) if isinstance(v, list) else None


def champ(nom):
    return lambda r: r.get(nom)


COULEUR = {"k": "color", "label": "Couleur", "filterable": True}


# --- Arborescence et gabarits ------------------------------------------------------
CATEGORIES = [
    # Racines et nœuds intermédiaires (pas de produits directement rattachés)
    {"_id": "composants", "name": "Composants", "parent": None},
    {"_id": "refroidissement", "name": "Refroidissement", "parent": "composants"},
    {"_id": "stockage", "name": "Stockage", "parent": None},
    {"_id": "peripheriques", "name": "Périphériques", "parent": None},
    {"_id": "reseau", "name": "Réseau", "parent": None},
    {"_id": "energie", "name": "Énergie", "parent": None},
    {"_id": "logiciels", "name": "Logiciels", "parent": None},

    # --- Composants ---
    {
        "_id": "processeurs", "name": "Processeurs", "parent": "composants",
        "source": "cpu", "skuPrefix": "CPU", "noun": "ce processeur",
        "specs": [
            {"k": "socket", "label": "Socket", "filterable": True, "derived": True, "get": socket_processeur},
            {"k": "core_count", "label": "Nombre de cœurs", "filterable": True},
            {"k": "core_clock", "label": "Fréquence de base", "unit": "GHz"},
            {"k": "boost_clock", "label": "Fréquence boost", "unit": "GHz"},
            {"k": "microarchitecture", "label": "Microarchitecture", "filterable": True},
            {"k": "tdp", "label": "TDP", "unit": "W", "filterable": True},
            {"k": "integrated_graphics", "label": "Partie graphique intégrée", "filterable": True,
             "get": lambda r: defaut("Aucune")(r.get("graphics"))},
        ],
    },
    {
        "_id": "cartes-meres", "name": "Cartes mères", "parent": "composants",
        "source": "motherboard", "skuPrefix": "MB", "noun": "cette carte mère",
        "specs": [
            {"k": "socket", "label": "Socket", "filterable": True},
            {"k": "form_factor", "label": "Format", "filterable": True},
            {"k": "max_memory", "label": "Mémoire maximale", "unit": "Go", "filterable": True},
            {"k": "memory_slots", "label": "Emplacements mémoire", "filterable": True},
            COULEUR,
        ],
    },
    {
        "_id": "memoire-ram", "name": "Mémoire RAM", "parent": "composants",
        "source": "memory", "skuPrefix": "RAM", "noun": "ce kit mémoire",
        "specs": [
            {"k": "ddr_generation", "label": "Type de mémoire", "filterable": True, "get": _ddr},
            {"k": "speed_mhz", "label": "Fréquence", "unit": "MHz", "filterable": True,
             "get": lambda r: r["speed"][1] if isinstance(r.get("speed"), list) else r.get("speed")},
            {"k": "capacity_gb", "label": "Capacité totale", "unit": "Go", "filterable": True, "derived": True,
             "get": lambda r: r["modules"][0] * r["modules"][1]},
            {"k": "module_count", "label": "Nombre de barrettes", "filterable": True, "get": lambda r: r["modules"][0]},
            {"k": "module_size_gb", "label": "Capacité par barrette", "unit": "Go", "get": lambda r: r["modules"][1]},
            {"k": "cas_latency", "label": "Latence CAS", "filterable": True},
            {"k": "first_word_latency", "label": "Latence du premier mot", "unit": "ns"},
            COULEUR,
        ],
    },
    {
        "_id": "cartes-graphiques", "name": "Cartes graphiques", "parent": "composants",
        "source": "video-card", "skuPrefix": "GPU", "noun": "cette carte graphique",
        "specs": [
            {"k": "chipset", "label": "Puce graphique", "filterable": True},
            {"k": "memory_gb", "label": "Mémoire vidéo", "unit": "Go", "filterable": True, "get": champ("memory")},
            {"k": "core_clock", "label": "Fréquence de base", "unit": "MHz"},
            {"k": "boost_clock", "label": "Fréquence boost", "unit": "MHz"},
            {"k": "length_mm", "label": "Longueur", "unit": "mm", "filterable": True, "get": champ("length")},
            COULEUR,
        ],
    },
    {
        "_id": "boitiers", "name": "Boîtiers", "parent": "composants",
        "source": "case", "skuPrefix": "CASE", "noun": "ce boîtier",
        "specs": [
            {"k": "case_type", "label": "Format du boîtier", "filterable": True, "get": champ("type")},
            {"k": "mb_form_factor", "label": "Cartes mères acceptées", "filterable": True, "derived": True,
             "get": lambda r: formats_carte_mere(r.get("type"))},
            {"k": "side_panel", "label": "Panneau latéral", "filterable": True},
            {"k": "included_psu_w", "label": "Alimentation fournie", "unit": "W", "get": champ("psu")},
            {"k": "external_volume_l", "label": "Volume", "unit": "L", "get": champ("external_volume")},
            {"k": "internal_35_bays", "label": "Baies 3,5 pouces internes", "filterable": True},
            COULEUR,
        ],
    },
    {
        "_id": "alimentations", "name": "Alimentations", "parent": "composants",
        "source": "power-supply", "skuPrefix": "PSU", "noun": "cette alimentation",
        "specs": [
            {"k": "psu_format", "label": "Format", "filterable": True, "get": champ("type")},
            {"k": "wattage", "label": "Puissance", "unit": "W", "filterable": True},
            {"k": "efficiency", "label": "Certification 80 PLUS", "filterable": True, "get": _efficacite},
            {"k": "modular", "label": "Modularité", "filterable": True, "get": _modularite},
            COULEUR,
        ],
    },
    {
        "_id": "ventirads", "name": "Ventirads et watercooling", "parent": "refroidissement",
        "source": "cpu-cooler", "skuPrefix": "COOL", "noun": "ce système de refroidissement",
        "specs": [
            {"k": "cooling_type", "label": "Type", "filterable": True, "derived": True,
             "get": lambda r: "Watercooling AIO" if vrai(r.get("size")) else "Ventirad à air"},
            {"k": "radiator_mm", "label": "Taille du radiateur", "unit": "mm", "filterable": True, "get": champ("size")},
            {"k": "fan_rpm_min", "label": "Vitesse min. du ventilateur", "unit": "tr/min", "get": lambda r: rmin(r.get("rpm"))},
            {"k": "fan_rpm_max", "label": "Vitesse max. du ventilateur", "unit": "tr/min", "get": lambda r: rmax(r.get("rpm"))},
            {"k": "noise_db_min", "label": "Niveau sonore min.", "unit": "dB", "get": lambda r: rmin(r.get("noise_level"))},
            {"k": "noise_db_max", "label": "Niveau sonore max.", "unit": "dB", "filterable": True,
             "get": lambda r: rmax(r.get("noise_level"))},
            COULEUR,
        ],
    },
    {
        "_id": "ventilateurs-boitier", "name": "Ventilateurs de boîtier", "parent": "refroidissement",
        "source": "case-fan", "skuPrefix": "FAN", "noun": "ce ventilateur",
        "specs": [
            {"k": "size_mm", "label": "Taille", "unit": "mm", "filterable": True, "get": champ("size")},
            {"k": "pwm", "label": "Contrôle PWM", "filterable": True},
            {"k": "fan_rpm_min", "label": "Vitesse min.", "unit": "tr/min", "get": lambda r: rmin(r.get("rpm"))},
            {"k": "fan_rpm_max", "label": "Vitesse max.", "unit": "tr/min", "get": lambda r: rmax(r.get("rpm"))},
            {"k": "airflow_cfm_max", "label": "Débit d'air max.", "unit": "CFM", "get": lambda r: rmax(r.get("airflow"))},
            {"k": "noise_db_max", "label": "Niveau sonore max.", "unit": "dB", "filterable": True,
             "get": lambda r: rmax(r.get("noise_level"))},
            COULEUR,
        ],
    },
    {
        "_id": "controleurs-ventilateurs", "name": "Contrôleurs de ventilateurs", "parent": "refroidissement",
        "source": "fan-controller", "skuPrefix": "FANC", "noun": "ce contrôleur",
        "specs": [
            {"k": "channels", "label": "Nombre de canaux", "filterable": True},
            {"k": "channel_wattage", "label": "Puissance par canal", "unit": "W"},
            {"k": "pwm", "label": "Contrôle PWM", "filterable": True},
            {"k": "bay_size", "label": "Taille de baie", "unit": "pouces", "get": champ("form_factor")},
            COULEUR,
        ],
    },
    {
        "_id": "pates-thermiques", "name": "Pâtes thermiques", "parent": "refroidissement",
        "source": "thermal-paste", "skuPrefix": "PASTE", "noun": "cette pâte thermique",
        "specs": [{"k": "amount_g", "label": "Quantité", "unit": "g", "get": champ("amount")}],
    },
    {
        "_id": "cartes-son", "name": "Cartes son", "parent": "composants",
        "source": "sound-card", "skuPrefix": "SND", "noun": "cette carte son",
        "specs": [
            {"k": "channels", "label": "Canaux", "filterable": True},
            {"k": "bit_depth", "label": "Résolution audio", "unit": "bits", "get": champ("digital_audio")},
            {"k": "snr_db", "label": "Rapport signal/bruit", "unit": "dB", "get": champ("snr")},
            {"k": "sample_rate_khz", "label": "Fréquence d'échantillonnage", "unit": "kHz", "get": champ("sample_rate")},
            {"k": "chipset", "label": "Puce audio"},
            {"k": "interface", "label": "Interface", "filterable": True},
        ],
    },
    {
        "_id": "lecteurs-optiques", "name": "Lecteurs et graveurs", "parent": "composants",
        "source": "optical-drive", "skuPrefix": "ODD", "noun": "ce lecteur",
        "specs": [
            {"k": "bluray", "label": "Compatible Blu-ray", "filterable": True, "derived": True,
             "get": lambda r: r.get("bd") is not None},
            {"k": "bd_read", "label": "Lecture Blu-ray", "unit": "x", "get": champ("bd")},
            {"k": "dvd_read", "label": "Lecture DVD", "unit": "x", "get": champ("dvd")},
            {"k": "cd_read", "label": "Lecture CD", "unit": "x", "get": champ("cd")},
            {"k": "bd_write", "label": "Écriture Blu-ray"},
            {"k": "dvd_write", "label": "Écriture DVD"},
            {"k": "cd_write", "label": "Écriture CD"},
        ],
    },
    {
        "_id": "accessoires-boitier", "name": "Accessoires de boîtier", "parent": "composants",
        "source": "case-accessory", "skuPrefix": "ACC", "noun": "cet accessoire",
        "specs": [
            {"k": "accessory_type", "label": "Type d'accessoire", "filterable": True, "get": champ("type")},
            {"k": "bay_size", "label": "Taille de baie", "unit": "pouces", "get": champ("form_factor")},
        ],
    },

    # --- Stockage ---
    {
        "_id": "disques-internes", "name": "SSD et disques durs internes", "parent": "stockage",
        "source": "internal-hard-drive", "skuPrefix": "DRV", "noun": "ce disque",
        "specs": [
            {"k": "drive_type", "label": "Technologie", "filterable": True, "get": _type_disque},
            {"k": "capacity_gb", "label": "Capacité", "unit": "Go", "filterable": True, "get": champ("capacity")},
            {"k": "rpm", "label": "Vitesse de rotation", "unit": "tr/min", "filterable": True,
             "get": lambda r: r.get("type") if est_nombre(r.get("type")) else None},
            {"k": "form_factor", "label": "Format", "filterable": True, "get": _format_disque},
            {"k": "interface", "label": "Interface", "filterable": True},
            {"k": "cache_mb", "label": "Cache", "unit": "Mo", "get": champ("cache")},
        ],
    },
    {
        "_id": "disques-externes", "name": "Disques externes", "parent": "stockage",
        "source": "external-hard-drive", "skuPrefix": "EXT", "noun": "ce disque externe",
        "specs": [
            {"k": "ext_type", "label": "Type", "filterable": True, "get": champ("type")},
            {"k": "capacity_gb", "label": "Capacité", "unit": "Go", "filterable": True, "get": champ("capacity")},
            {"k": "interface", "label": "Connectique", "filterable": True, "get": lambda r: liste(r.get("interface"))},
            COULEUR,
        ],
    },

    # --- Périphériques ---
    {
        "_id": "ecrans", "name": "Écrans", "parent": "peripheriques",
        "source": "monitor", "skuPrefix": "MON", "noun": "cet écran",
        "specs": [
            {"k": "screen_size", "label": "Taille d'écran", "unit": "pouces", "filterable": True},
            {"k": "resolution", "label": "Résolution", "filterable": True, "get": _resolution},
            {"k": "refresh_rate", "label": "Fréquence de rafraîchissement", "unit": "Hz", "filterable": True},
            {"k": "response_time", "label": "Temps de réponse", "unit": "ms", "filterable": True},
            {"k": "panel_type", "label": "Type de dalle", "filterable": True},
            {"k": "aspect_ratio", "label": "Format d'image", "filterable": True},
        ],
    },
    {
        "_id": "claviers", "name": "Claviers", "parent": "peripheriques",
        "source": "keyboard", "skuPrefix": "KBD", "noun": "ce clavier",
        "specs": [
            {"k": "style", "label": "Usage", "filterable": True},
            {"k": "switches", "label": "Switchs", "filterable": True},
            {"k": "backlit", "label": "Rétroéclairage", "filterable": True},
            {"k": "tenkeyless", "label": "Sans pavé numérique (TKL)", "filterable": True},
            {"k": "connection_type", "label": "Connexion", "filterable": True, "get": lambda r: liste(r.get("connection_type"))},
            COULEUR,
        ],
    },
    {
        "_id": "souris", "name": "Souris", "parent": "peripheriques",
        "source": "mouse", "skuPrefix": "MOUSE", "noun": "cette souris",
        "specs": [
            {"k": "tracking_method", "label": "Capteur", "filterable": True},
            {"k": "connection_type", "label": "Connexion", "filterable": True, "get": lambda r: liste(r.get("connection_type"))},
            {"k": "max_dpi", "label": "Résolution maximale", "unit": "DPI", "filterable": True},
            {"k": "hand_orientation", "label": "Orientation", "filterable": True},
            COULEUR,
        ],
    },
    {
        "_id": "casques-audio", "name": "Casques audio", "parent": "peripheriques",
        "source": "headphones", "skuPrefix": "HEAD", "noun": "ce casque",
        "specs": [
            {"k": "headphone_type", "label": "Type", "filterable": True, "get": champ("type")},
            {"k": "microphone", "label": "Micro intégré", "filterable": True},
            {"k": "wireless", "label": "Sans fil", "filterable": True},
            {"k": "enclosure_type", "label": "Conception", "filterable": True},
            {"k": "freq_min_hz", "label": "Réponse en fréquence min.", "unit": "Hz",
             "get": lambda r: rmin(r.get("frequency_response"))},
            {"k": "freq_max_khz", "label": "Réponse en fréquence max.", "unit": "kHz",
             "get": lambda r: rmax(r.get("frequency_response"))},
            COULEUR,
        ],
    },
    {
        "_id": "enceintes", "name": "Enceintes", "parent": "peripheriques",
        "source": "speakers", "skuPrefix": "SPK", "noun": "ce kit d'enceintes",
        "specs": [
            {"k": "configuration", "label": "Configuration", "filterable": True},
            {"k": "wattage", "label": "Puissance", "unit": "W", "filterable": True},
            {"k": "freq_min_hz", "label": "Réponse en fréquence min.", "unit": "Hz",
             "get": lambda r: rmin(r.get("frequency_response"))},
            {"k": "freq_max_khz", "label": "Réponse en fréquence max.", "unit": "kHz",
             "get": lambda r: rmax(r.get("frequency_response"))},
            COULEUR,
        ],
    },
    {
        "_id": "webcams", "name": "Webcams", "parent": "peripheriques",
        "source": "webcam", "skuPrefix": "CAM", "noun": "cette webcam",
        "specs": [
            {"k": "resolution", "label": "Résolutions vidéo", "filterable": True, "get": champ("resolutions")},
            {"k": "connection", "label": "Connexion"},
            {"k": "focus_type", "label": "Mise au point", "filterable": True},
            {"k": "compatible_os", "label": "Systèmes compatibles", "filterable": True, "get": champ("os")},
            {"k": "fov_deg", "label": "Champ de vision", "unit": "°", "get": champ("fov")},
        ],
    },

    # --- Réseau ---
    {
        "_id": "cartes-reseau", "name": "Cartes réseau filaires", "parent": "reseau",
        "source": "wired-network-card", "skuPrefix": "NET", "noun": "cette carte réseau",
        "specs": [{"k": "interface", "label": "Interface", "filterable": True}, COULEUR],
    },
    {
        "_id": "cartes-wifi", "name": "Cartes Wi-Fi", "parent": "reseau",
        "source": "wireless-network-card", "skuPrefix": "WIFI", "noun": "cette carte Wi-Fi",
        "specs": [
            {"k": "protocol", "label": "Norme Wi-Fi", "filterable": True},
            {"k": "interface", "label": "Interface", "filterable": True},
            COULEUR,
        ],
    },

    # --- Énergie ---
    {
        "_id": "onduleurs", "name": "Onduleurs", "parent": "energie",
        "source": "ups", "skuPrefix": "UPS", "noun": "cet onduleur",
        "specs": [
            {"k": "capacity_w", "label": "Puissance", "unit": "W", "filterable": True},
            {"k": "capacity_va", "label": "Puissance apparente", "unit": "VA", "filterable": True},
        ],
    },

    # --- Logiciels (type "software" : Polymorphic pattern) ---
    {
        "_id": "systemes-exploitation", "name": "Systèmes d'exploitation", "parent": "logiciels",
        "source": "os", "skuPrefix": "SOFT", "noun": "ce logiciel", "productType": "software",
        "specs": [
            {"k": "architecture_bits", "label": "Architecture", "unit": "bits", "filterable": True, "get": champ("mode")},
            {"k": "max_memory_gb", "label": "Mémoire maximale gérée", "unit": "Go", "get": champ("max_memory")},
        ],
        "extra": lambda r: {"software": infos_logiciel(r["name"])},
    },
]


def construire_specs(categorie, ligne):
    """Transforme une ligne source en liste d'attributs {k, v, u?} (Attribute pattern)."""
    specs = []
    for s in categorie["specs"]:
        brut = s["get"](ligne) if "get" in s else ligne.get(s["k"])
        for v in (brut if isinstance(brut, list) else [brut]):
            if v is None or (isinstance(v, float) and v != v):
                continue
            attribut = {"k": s["k"], "v": v}
            if s.get("unit"):
                attribut["u"] = s["unit"]
            specs.append(attribut)
    return specs
