#!/usr/bin/env python3
"""
Entry point for iCloud MCP Server.

Usage:
    python run.py              # Run with stdio transport (local)
    python run.py --http       # Run with SSE transport (server/Railway)
"""

import sys
import os
import traceback


def main():
    try:
        print(f"[icloud-mcp] Starting server...", flush=True)
        print(f"[icloud-mcp] MCP_TRANSPORT={os.environ.get('MCP_TRANSPORT')}", flush=True)
        print(f"[icloud-mcp] MCP_AUTH_TOKEN={'set' if os.environ.get('MCP_AUTH_TOKEN') else 'not set'}", flush=True)
        print(f"[icloud-mcp] MCP_BASE_URL={os.environ.get('MCP_BASE_URL')}", flush=True)
        print(f"[icloud-mcp] PORT={os.environ.get('PORT')}", flush=True)

        from icloud_mcp.server import main as server_main
        print(f"[icloud-mcp] Server module imported successfully", flush=True)

        server_main()
    except Exception as e:
        print(f"[icloud-mcp] FATAL ERROR: {e}", flush=True)
        traceback.print_exc()
        # Keep process alive briefly so Railway captures logs
        import time
        time.sleep(30)
        sys.exit(1)


if __name__ == "__main__":
    main()
