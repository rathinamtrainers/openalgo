"""Inspect OpenAlgo on GCE. Never prints secrets."""

from pathlib import Path
import subprocess

env = Path("/opt/openalgo/current/.env")
print("env", env.exists())
if env.exists():
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(("HOST_SERVER", "WEBSOCKET_URL", "CORS_ALLOWED")):
            print(line)

for path in (
    Path("/opt/openalgo/current/utils/version.py"),
    Path("/opt/openalgo/current/pyproject.toml"),
):
    if path.exists():
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
            if "VERSION" in line or line.startswith("version"):
                print(path.name, line.strip())
                if i > 8 and path.name == "pyproject.toml":
                    break

app = Path("/opt/openalgo/current/app.py")
print("gex_bp", "gex_bp" in app.read_text(encoding="utf-8", errors="replace") if app.exists() else "no app")
print("greeks_lab", "greeks_lab" in app.read_text(encoding="utf-8", errors="replace") if app.exists() else "no app")

react = Path("/opt/openalgo/current/blueprints/react_app.py")
print("react greekslab", "greekslab" in react.read_text(encoding="utf-8", errors="replace") if react.exists() else "no react")

index = Path("/opt/openalgo/current/frontend/dist/index.html")
print("dist index", index.exists(), index.stat().st_mtime if index.exists() else "")
