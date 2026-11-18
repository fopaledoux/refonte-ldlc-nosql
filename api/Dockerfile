# Prototype d'API de la refonte LDLC (FastAPI)
# Le code (app/) et la démonstration (demo.py) sont montés par docker-compose, en lecture seule.
FROM python:3.11-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
