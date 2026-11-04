"""Add public /tick-status doors to the GCE nginx vhost. No orders."""

from pathlib import Path

PATHS = [
    Path("/etc/nginx/sites-enabled/desk.zerotouchalgo.com"),
    Path("/etc/nginx/sites-available/desk.zerotouchalgo.com"),
]

BLOCK = """
    location = /tick-status {
        alias /var/lib/strike-desk/desk-health-ticks.html;
        default_type text/html;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "*";
    }

    location = /tick-status.html {
        alias /var/lib/strike-desk/desk-health-ticks.html;
        default_type text/html;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "*";
    }

    location = /tick-status.jsonl {
        alias /var/lib/strike-desk/desk-health-ticks.jsonl;
        default_type application/x-ndjson;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "*";
    }
"""


def _ssl_insert(text: str) -> str:
    if "location = /tick-status" in text:
        return text
    ssl_at = text.find("listen 443")
    if ssl_at < 0:
        raise SystemExit("no 443 server")
    loc = text.find("    location / {", ssl_at)
    if loc < 0:
        raise SystemExit("no ssl location /")
    return text[:loc] + BLOCK + "\n" + text[loc:]


def main() -> None:
    seen: set[Path] = set()
    changed = False
    for path in PATHS:
        if not path.exists():
            continue
        real = path.resolve()
        if real in seen:
            continue
        seen.add(real)
        text = path.read_text(encoding="utf-8")
        updated = _ssl_insert(text)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            print("patched", path)
            changed = True
        else:
            print("already", path)
    if not changed and not seen:
        raise SystemExit("nginx vhost missing")


if __name__ == "__main__":
    main()
