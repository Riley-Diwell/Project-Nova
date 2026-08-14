# NOVA backend - deployed to Cloud Run (see Nova_V1_Build_Plan_PDD.md Section 3,
# "hosted cloud" backend). Local dev still uses `uvicorn --reload` directly
# (see README.md); this image is for the deployed service only.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Persona embeddings (app/persona/embeddings.py) load BAAI/bge-large-en-v1.5
# (~1.2GB) via fastembed on first use. Cloud Run scales to zero between
# requests, so without this the model would re-download from HuggingFace on
# every cold start. Baking it into the image means a cold start only ever
# loads it from local disk. FASTEMBED_CACHE_PATH is set before the warm-up
# run so build time and run time agree on where the cache lives.
ENV FASTEMBED_CACHE_PATH=/app/.fastembed_cache
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-large-en-v1.5')"

COPY app/ ./app/

# Cloud Run injects $PORT (defaults to 8080) and expects the container to
# listen on it; main.py lives in app/ per the existing `cd backend/app &&
# uvicorn main:app` layout (README.md), not importable as backend.app.main.
WORKDIR /app/app
ENV PORT=8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
