FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/

# Install package
RUN pip install --no-cache-dir .

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app

USER app

# Cloud Run uses PORT env variable (default 8080, but we prefer 8000)
ENV PORT=8000
EXPOSE 8000

# Health check — uses OAuth metadata endpoint (public, no auth required)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/.well-known/oauth-authorization-server || curl -f http://localhost:${PORT}/health || exit 1

# Run in SSE mode for Railway
ENV MCP_TRANSPORT=sse
COPY run.py ./

CMD ["python", "-u", "run.py", "--http"]
