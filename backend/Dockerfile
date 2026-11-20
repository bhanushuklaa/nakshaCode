FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown app:app /data
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

USER app
# Grammars are downloaded once at build time so the server never needs internet.
RUN python -c "import tree_sitter_language_pack as t; t.download(['python', 'javascript', 'typescript', 'tsx'])"

COPY --chown=app:app app ./app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
