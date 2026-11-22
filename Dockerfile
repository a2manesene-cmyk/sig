FROM python:3.11-slim

# libpq/GEOS/PROJ : PostGIS ; tesseract + poppler : lecture des fiches techniques (scans, PDF)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    libgeos-dev \
    libproj-dev \
    tesseract-ocr \
    tesseract-ocr-fra \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

EXPOSE 5000

# Gunicorn (serveur de production) ; délai long pour les gros imports GeoJSON
CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:5000", "--timeout", "900", "run:app"]
