FROM python:3.11-slim

WORKDIR /app

# Ghostscript for PDF → JPEG conversion before IPP print
RUN apt-get update && apt-get install -y --no-install-recommends ghostscript && rm -rf /var/lib/apt/lists/*

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY printer.py .

# Flush PDF and state are mounted at runtime — not baked into the image
VOLUME ["/data"]

CMD ["python", "printer.py"]
