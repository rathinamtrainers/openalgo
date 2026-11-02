"""Install CoA buy-alert HTML and nginx door. Does not restart CoA."""

from pathlib import Path
import shutil
import subprocess
import sys

SRC = Path("/tmp/sd-coa/coa-alert.html")
DEST = Path("/var/lib/strike-desk/coa-alert.html")
NGINX_PATHS = [
    Path("/etc/nginx/sites-enabled/desk.zerotouchalgo.com"),
    Path("/etc/nginx/sites-available/desk.zerotouchalgo.com"),
]

ALERT = """
    location = /coa-alert {
        alias /var/lib/strike-desk/coa-alert.html;
        default_type text/html;
        add_header Cache-Control "no-store";
    }

    location = /coa-alert.html {
        alias /var/lib/strike-desk/coa-alert.html;
        default_type text/html;
        add_header Cache-Control "no-store";
    }
"""


def _ssl_insert(text: str) -> str:
    if "location = /coa-alert" in text:
        return text
    ssl_at = text.find("listen 443")
    if ssl_at < 0:
        raise SystemExit("no 443 server")
    loc = text.find("    location / {", ssl_at)
    if loc < 0:
        raise SystemExit("no ssl location /")
    return text[:loc] + ALERT + "\n" + text[loc:]


def main() -> None:
    if not SRC.exists():
        raise SystemExit("missing " + str(SRC))
    DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SRC, DEST)
    DEST.chmod(0o644)
    print("wrote", DEST, DEST.stat().st_size)
    seen: set[Path] = set()
    for path in NGINX_PATHS:
        if not path.exists():
            continue
        real = path.resolve()
        if real in seen:
            continue
        seen.add(real)
        text = _ssl_insert(path.read_text(encoding="utf-8"))
        path.write_text(text, encoding="utf-8")
        print("patched", path)
    test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    sys.stderr.write(test.stderr)
    if test.returncode != 0:
        raise SystemExit("nginx -t failed")
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
    print("nginx reloaded")


if __name__ == "__main__":
    main()
