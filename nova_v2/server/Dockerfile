# NOVA V2 backend - deployed to Cloud Run. Local dev uses
# `uvicorn app.main:app --reload` directly from this directory (see README.md);
# this image is for the deployed service only.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Persona embeddings (app/store/persona/embeddings.py) load BAAI/bge-large-en-v1.5
# (~1.2GB) via fastembed on first use. Cloud Run scales to zero between
# requests, so without this the model would re-download from HuggingFace on
# every cold start. Baking it into the image means a cold start only ever
# loads it from local disk. FASTEMBED_CACHE_PATH is set before the warm-up
# run so build time and run time agree on where the cache lives.
ENV FASTEMBED_CACHE_PATH=/app/.fastembed_cache
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-large-en-v1.5')"

COPY app/ ./app/

# Cloud Run injects $PORT (defaults to 8080) and expects the container to
# listen on it. Unlike V1 (which needed a WORKDIR trick because main.py only
# ran as a bare module from inside backend/app/), app.main:app works directly
# from /app - that's the whole point of the V2 import restructure.
ENV PORT=8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
