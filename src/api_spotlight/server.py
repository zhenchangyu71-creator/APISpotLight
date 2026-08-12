"""FastMCP stdio server for APISpotLight's two-stage workflow."""

from __future__ import annotations

from fastmcp import FastMCP

from api_spotlight.workflows import (
    export_confirmed_apis as _export_confirmed_apis,
    find_page_apis as _find_page_apis,
)

mcp = FastMCP(
    "APISpotLight",
    instructions=(
        "Find page-relevant API candidates from local evidence, let the operator "
        "review selected flags, then export the confirmed OpenAPI subset."
    ),
)

mcp.tool(
    name="find_page_apis",
    description=(
        "Match mock paths and optional screenshot evidence against a complete "
        "local OpenAPI document, then write candidates.json and candidates.md."
    ),
)(_find_page_apis)

mcp.tool(
    name="export_confirmed_apis",
    description=(
        "Read an edited candidates.json or accept confirmed_apis directly, then "
        "export selected APIs with recursively referenced OpenAPI components."
    ),
)(_export_confirmed_apis)


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
