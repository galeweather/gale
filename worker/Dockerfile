FROM python:3.11-slim

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        gdal-bin \
        python3-gdal \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENTRYPOINT ["python3", "main.py"]
