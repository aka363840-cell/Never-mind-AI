# Ella Gaming server (API + Mini App frontend). Build from this folder:  docker build -t ella-gaming .
# Not built/run in the authoring sandbox (no Docker there) - the same commands were run directly with Python instead.
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 ELLA_DATA_DIR=/data PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
# Playwright's bundled node needs libatomic (missing in the slim image)
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
# Chromium for the runtime ad check (adds ~400 MB). Use --build-arg WITH_BROWSER=0 to skip; games that are not provably ad-free then stay hidden.
ARG WITH_BROWSER=1
RUN if [ "$WITH_BROWSER" = "1" ]; then playwright install --with-deps chromium; fi
COPY ella_server ./ella_server
COPY web ./web
# Railway rejects the VOLUME keyword (attach a Railway volume at /data instead) and mounts volumes as root, so the server runs as root;
# Chromium then needs --no-sandbox (see scanner.py), which is fine inside the container.
RUN mkdir -p /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.getenv('PORT','8080'),timeout=4)"
# ONE worker on purpose: rate limits and the scan state are in memory. It is async and handles many players at once.
CMD ["python", "-m", "ella_server"]
