# Sentry Ward in a container. Data lives in the /data volume so it survives rebuilds.
#   docker build -t sentry-ward .
#   docker run --rm -v ward-data:/data -e SEC_USER_AGENT="Jane Doe jane@example.com" sentry-ward download
#   docker run --rm -p 8765:8765 -v ward-data:/data sentry-ward live --host 0.0.0.0 --no-browser
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WARD_DATA=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && useradd --create-home ward && mkdir -p /data && chown ward /data

# The fund universe ships with the image; `ward build` reads data/universe.csv from here.
COPY data/universe.csv ./data/universe.csv
COPY PREREGISTRATION.md RESULTS.md ./

USER ward
VOLUME ["/data"]
EXPOSE 8765
ENTRYPOINT ["ward"]
CMD ["--help"]
