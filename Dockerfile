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

COPY requirements.txt .
COPY pyproject.toml .
COPY README.md .

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/config.example.yaml ./config/

# Install the package
RUN pip install --no-cache-dir .

# Verify installation in builder
RUN python -c "import secret_rotator; print(f'Builder: secret_rotator {secret_rotator.__version__} installed')"

# Stage 2: Runtime - Minimal production image
FROM python:3.11-slim AS runtime

LABEL maintainer="othaimeen.dev@gmail.com"
LABEL description="Secret Rotation System - Production Runtime"
LABEL version="1.1.0"

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

# Copy application code
COPY --from=builder /build/config/config.example.yaml /app/config/config.example.yaml

# Set up directory structure with proper permissions
RUN mkdir -p /app/config /app/data /app/data/backup /app/logs && \
    chown -R secretrotator:secretrotator /app

# Copy entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch to non-root user
USER secretrotator

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SECRET_ROTATOR_CONFIG=/app/config/config.yaml

# Expose web interface port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8080/api/status || exit 1

# Set volumes for persistent data
VOLUME ["/app/config", "/app/data", "/app/logs"]

# Use entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Default command
CMD ["secret-rotator"]