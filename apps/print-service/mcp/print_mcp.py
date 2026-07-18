#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0"]
# ///
"""print-mcp - MCP server wrapping the home 3D-print capability (apps/print-service).

The scoped, structured surface for the print capability: exposes slice / print /
status / cancel as MCP tools so Claude Code, OpenClaw, and (later) the voice
orchestrator can drive 3D printing with typed calls instead of raw HTTP. Pairs
with the `print` CLI (the shell/skill surface).

Runs with no persistent install: `uv run` reads the inline PEP 723 deps and
builds an ephemeral env. Register in .mcp.json:

  "print": { "command": "uv", "args": ["run", "<repo>/apps/print-service/mcp/print_mcp.py"] }

Endpoint: $PRINT_SERVICE_URL (default https://print.kblab.me), LAN/Tailscale only.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("PRINT_SERVICE_URL", "https://print.kblab.me").rstrip("/")
mcp = FastMCP("print")


def _call(method, path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"error": e.read().decode(errors="replace"), "status": e.code}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def list_printers() -> dict:
    """List known 3D printers and their available filament and process options."""
    return _call("GET", "/printers", timeout=20)


@mcp.tool()
def slice_model(file: str, printer: str = "neptune", filament: str = "pla",
                process: str = "standard") -> dict:
    """Slice an STL (a filename under the NAS 3d-printing share) to gcode. Does NOT print.

    filament: pla|petg. process: standard(0.20mm)|fine(0.12mm)|draft(0.24mm).
    """
    return _call("POST", "/slice", {"file": file, "printer": printer,
                                    "filament": filament, "process": process})


@mcp.tool()
def print_model(file: str, printer: str = "neptune", filament: str = "pla",
                process: str = "standard", start: bool = True) -> dict:
    """Slice an STL, upload it to the printer, and (by default) start the print.

    Set start=False to upload without starting. Requires the printer powered on.
    """
    return _call("POST", "/print", {"file": file, "printer": printer, "filament": filament,
                                    "process": process, "start": start})


@mcp.tool()
def printer_status(printer: str = "neptune") -> dict:
    """Get the printer's current state (print_stats / webhooks / display_status)."""
    return _call("GET", "/status?printer=" + urllib.parse.quote(printer), timeout=20)


@mcp.tool()
def cancel_print(printer: str = "neptune") -> dict:
    """Cancel the current print on the given printer."""
    return _call("POST", "/cancel", {"printer": printer}, timeout=30)


if __name__ == "__main__":
    mcp.run()
