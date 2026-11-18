# Lot 1 — image des scripts Python (chargement, requêtes types, exports)
# L'image mongo:8 ne contient pas Python : les scripts tournent dans ce conteneur
# et se connectent au service « mongodb ».
FROM python:3.11-slim
RUN pip install --no-cache-dir pymongo==4.10.1
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /lot1
