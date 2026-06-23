# Kronos Range Service — heavy image (torch + Kronos model). Runs SEPARATELY from
# the lean main backend. Build context = repo root:  docker build -f services/kronos/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

# git needed to clone the (gitignored) Kronos vendor code at build time.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install heavy deps first (cached layer). CPU torch wheel keeps the image smaller.
COPY services/kronos/requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

# Clone the Kronos model code (gitignored in our repo, vendored separately).
RUN git clone --depth 1 https://github.com/shiyu-coder/Kronos.git vendor/Kronos

# Shared backend modules the service reuses (the range adapter + its data deps).
COPY backend/__init__.py backend/__init__.py
COPY backend/signals/__init__.py backend/signals/kronos_range.py backend/signals/
COPY backend/data backend/data

# The service itself.
COPY services/kronos/main.py services/kronos/main.py
COPY services/__init__.py services/__init__.py
COPY services/kronos/__init__.py services/kronos/__init__.py

ENV PYTHONUNBUFFERED=1
# Pre-download model weights at build so the first request isn't a cold 90s+ download.
RUN python -c "import sys; sys.path.insert(0,'vendor/Kronos'); \
from model import Kronos, KronosTokenizer; \
KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-base'); \
Kronos.from_pretrained('NeoQuasar/Kronos-small'); \
print('Kronos weights cached')"

EXPOSE 8012
CMD ["sh", "-c", "uvicorn services.kronos.main:app --host 0.0.0.0 --port ${PORT:-8012}"]
