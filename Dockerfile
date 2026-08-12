FROM python:3.12-slim

WORKDIR /app

# Dependencies first, from the lock, in their own layer — this is the whole
# point: `pip install .` re-resolved on every build and let mcp 2.0.0 in on
# 2026-08-11. Nothing moves now without regenerating requirements.lock.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Then the package itself. --no-deps is load-bearing: without it pip re-reads
# the ranges in pyproject.toml and is free to upgrade what the lock just
# pinned, which would silently undo the line above.
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

# Railway sets PORT env var automatically
ENV MCP_TRANSPORT=sse
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["icloud-mcp", "--http"]
