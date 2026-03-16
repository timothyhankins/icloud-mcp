FROM python:3.12-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/

# Install package
RUN pip install --no-cache-dir .

# Railway sets PORT env var automatically
ENV MCP_TRANSPORT=sse
ENV HOST=0.0.0.0

EXPOSE 8000

CMD ["icloud-mcp", "--http"]
