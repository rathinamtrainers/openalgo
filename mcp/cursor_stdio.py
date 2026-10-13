"""Launch the OpenAlgo stdio MCP without putting the API key on the argv of mcp.json.

Reads OPENALGO_API_KEY from the process env or from
``%USERPROFILE%\\.cursor\\openalgo-mcp.env``. Host defaults to the laptop
tunnel: http://127.0.0.1:5000.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def main() -> None:
    _load_env_file(Path.home() / ".cursor" / "openalgo-mcp.env")
    key = os.environ.get("OPENALGO_API_KEY") or os.environ.get("OPENALGO_MCP_API_KEY")
    host = os.environ.get("OPENALGO_API_HOST", "http://127.0.0.1:5000")
    if not key:
        raise SystemExit(
            "OPENALGO_API_KEY is missing. Put it in "
            + str(Path.home() / ".cursor" / "openalgo-mcp.env")
        )
    server = Path(__file__).with_name("mcpserver.py")
    sys.argv = [str(server), key, host]
    runpy.run_path(str(server), run_name="__main__")


if __name__ == "__main__":
    main()
