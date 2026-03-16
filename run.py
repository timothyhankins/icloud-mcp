#!/usr/bin/env python3
"""
Entry point for iCloud MCP Server.

Usage:
    python run.py              # Run with stdio transport (local)
    python run.py --http       # Run with SSE transport (server/Railway)
"""

import sys
import os


def main():
    # Import after parsing to allow --help without dependencies
    try:
        # Try installed package first (for Docker/production)
        from icloud_mcp.server import main as server_main
    except ImportError:
        # Fall back to development mode
        from src.icloud_mcp.server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
