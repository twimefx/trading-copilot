# Backend production image — FastAPI Copilot WITHOUT Kronos (lean for cheap hosting).
# Kronos forecasting runs as a separate optional service; the Copilot works without it.
FROM python:3.12-slim

WORKDIR /app

# System deps kept minimal (no torch in this image)
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY backend ./backend

ENV PYTHONUNBUFFERED=1
EXPOSE 8011

# Railway/Fly inject $PORT; default to 8011 locally
CMD ["sh", "-c", "uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8011}"]
