"""FastMCP server import and tool-registration contract tests."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from api_spotlight.server import mcp


def test_server_exposes_fastmcp_instance() -> None:
    assert isinstance(mcp, FastMCP)


def test_server_registers_exactly_two_public_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "find_page_apis",
        "export_confirmed_apis",
    }
    assert len(tools) == 2

    find_props = by_name["find_page_apis"].parameters["properties"]
    assert set(find_props) >= {
        "mock_paths",
        "screenshot_paths",
        "api_doc_path",
        "output_dir",
        "vision_enabled",
    }

    export_props = by_name["export_confirmed_apis"].parameters["properties"]
    assert set(export_props) >= {
        "api_doc_path",
        "candidates_path",
        "confirmed_apis",
        "output_path",
        "output_format",
    }
    assert set(by_name["export_confirmed_apis"].parameters["required"]) == {
        "api_doc_path",
        "output_path",
    }
