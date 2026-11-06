# SPARK local release: the container image. It holds only the tools (Python, Node, git)
# and bootstrap.sh; the app itself is pulled from the repository on every start, and
# deploy/local/start.sh in the pulled code does the rest.
# docker-compose.yml builds this straight from GitHub, so users only need that one file.
FROM node:22-bookworm-slim AS node

FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version
COPY bootstrap.sh /usr/local/bin/spark-bootstrap
RUN sed -i 's/\r$//' /usr/local/bin/spark-bootstrap && chmod 755 /usr/local/bin/spark-bootstrap
ENV SPARK_SRC=/opt/spark/src SPARK_CACHE=/opt/spark/cache
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15m --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)"
CMD ["spark-bootstrap"]
