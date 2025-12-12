"""
MCP Server entry point for module execution.

This file allows the MCP server to be run as a Python module:
    python -m maa_mcp

It imports and runs the main MCP server from main.py.
"""
from .main import mcp

if __name__ == "__main__":
    mcp.run()
