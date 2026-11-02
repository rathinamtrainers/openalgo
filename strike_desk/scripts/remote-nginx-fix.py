from pathlib import Path

PATHS = [
    Path("/etc/nginx/sites-enabled/desk.zerotouchalgo.com"),
    Path("/etc/nginx/sites-available/desk.zerotouchalgo.com"),
]

BTC = """
    location = /public-brief-btc-options.json {
        alias /var/lib/strike-desk/public-brief-btc-options.json;
        default_type application/json;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "https://zerotouchalgo.com";
        add_header Access-Control-Allow-Methods "GET, OPTIONS";
    }
"""

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

BN = """
    location = /public-brief-banknifty.json {
        alias /var/lib/strike-desk/public-brief-banknifty.json;
        default_type application/json;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "https://zerotouchalgo.com";
        add_header Access-Control-Allow-Methods "GET, OPTIONS";
    }
"""

SX = """
    location = /public-brief-sensex.json {
        alias /var/lib/strike-desk/public-brief-sensex.json;
        default_type application/json;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "https://zerotouchalgo.com";
        add_header Access-Control-Allow-Methods "GET, OPTIONS";
    }
"""

STRAY = """
    location = /public-brief-btc-options.json {
        alias /var/lib/strike-desk/public-brief-btc-options.json;
        default_type application/json;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "https://zerotouchalgo.com";
        add_header Access-Control-Allow-Methods "GET, OPTIONS";
    }
"""


def _ssl_insert(text: str) -> str:
    ssl_at = text.find("listen 443")
    if ssl_at < 0:
        raise SystemExit("no 443 server")
    loc = text.find("    location / {", ssl_at)
    if loc < 0:
        raise SystemExit("no ssl location /")
    extra = ""
    chunk = text[ssl_at:loc]
    if "public-brief-banknifty.json" not in chunk:
        extra += BN
    if "public-brief-sensex.json" not in chunk:
        extra += SX
    if "public-brief-btc-options.json" not in chunk:
        extra += BTC
    if "location = /coa-alert" not in chunk:
        extra += ALERT
    if not extra:
        return text
    return text[:loc] + extra + "\n" + text[loc:]


def main() -> None:
    seen: set[Path] = set()
    for path in PATHS:
        real = path.resolve()
        if real in seen or not path.exists():
            continue
        seen.add(real)
        text = path.read_text(encoding="utf-8")
        text = text.replace(STRAY, "\n")
        text = _ssl_insert(text)
        path.write_text(text, encoding="utf-8")
        print("patched", path)


if __name__ == "__main__":
    main()
