# Multi-stage Dockerfile for Secret Rotator
# Stage 1: Builder - Install dependencies and build wheels
FROM python:3.11-slim AS builder

LABEL maintainer="othaimeen.dev@gmail.com"
LABEL description="Secret Rotation System - Builder Stage"

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock.txt .
COPY pyproject.toml .
COPY README.md .

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install from the hash-pinned lock file, not requirements.txt: this is
# the whole point of generating requirements.lock.txt with
# `pip-compile --generate-hashes` (see CI's dependency-management docs) -
# --require-hashes refuses to install anything (including a transitive
# dependency) that doesn't match a pinned hash, closing the same
# dependency-confusion/supply-chain gap the mysql_connector_repackaged
# fix addressed. Until now the lock file was generated but never
# actually installed from anywhere.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --require-hashes -r requirements.lock.txt

COPY src/ ./src/
COPY config/config.example.yaml ./config/

# --no-deps: dependencies are already satisfied, hash-pinned, above.
# Without it, this step would let pip re-resolve pyproject.toml's
# unpinned `>=` ranges and silently pull in a newer, unhashed version
# of something the lock file just pinned.
RUN pip install --no-cache-dir --no-deps .

# Verify installation in builder
RUN python -c "import secret_rotator; print(f'Builder: secret_rotator {secret_rotator.__version__} installed')"

# Stage 2: Runtime - Minimal production image
FROM python:3.11-slim AS runtime

LABEL maintainer="othaimeen.dev@gmail.com"
LABEL description="Secret Rotation System - Production Runtime"
# Bump alongside the version in pyproject.toml on every release - this
# is intentionally static rather than derived at build time, so keep
# `docker build` and `bumpversion`/release steps in the same commit.
LABEL version="1.3.1"

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r secretrotator && \
    useradd -r -g secretrotator -u 1000 -m -s /bin/bash secretrotator

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy example configuration (will be used if no custom config provided)
COPY --from=builder /build/config/config.example.yaml /app/config/config.example.yaml

# Create directory structure
# NOTE: These directories will be overridden by volume mounts
# but we create them here to ensure proper ownership
RUN mkdir -p /app/config /app/data /app/data/backup /app/data/key_backups /app/logs && \
    chown -R secretrotator:secretrotator /app

# Copy entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
# Fix line endings and make executable
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Switch to non-root user
USER secretrotator

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8080/api/healthz || exit 1

# Set volumes for persistent data
# IMPORTANT: These are declaration only. Actual volumes defined in docker-compose.yml
VOLUME ["/app/data", "/app/logs"]

ENTRYPOINT ["/entrypoint.sh"]

CMD ["secret-rotator"]