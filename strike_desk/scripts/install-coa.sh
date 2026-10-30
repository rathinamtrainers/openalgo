#!/usr/bin/env bash
# Overlay CoA live onto the GCE strike-desk tree and start its unit.
# Does not restart strike-desk.service (StrikeShield stays up).
# Never prints the Hands token.
set -euo pipefail

SRC=${1:-/tmp/sd-coa}
DEST=/opt/strike-desk/current/strike_desk/src/strike_desk
ENV=/etc/strike-desk/strike-desk.env
TOKEN_FILE=$SRC/tt-coa.token
UNIT_SRC=$SRC/strike-desk-coa.service

if [[ ! -f $TOKEN_FILE ]]; then
  echo "missing CoA token file" >&2
  exit 1
fi
if [[ ! -f $UNIT_SRC ]]; then
  echo "missing strike-desk-coa.service" >&2
  exit 1
fi

TOKEN=$(tr -d '\r\n' <"$TOKEN_FILE")
if [[ -z $TOKEN ]]; then
  echo "empty CoA token" >&2
  exit 1
fi

install -d -m 0755 "$DEST" /var/lib/strike-desk
if [[ -f $SRC/coa-alert.html ]]; then
  install -m 0644 -o strikedesk -g strikedesk "$SRC/coa-alert.html" /var/lib/strike-desk/coa-alert.html
fi
for f in ltp_coa.py ltp_coa_exits.py ltp_coa_brief.py ltp_coa_replay.py ltp_coa_live.py \
         ltp_coa_intel.py ltp_coa_narrate.py tradetron_hands.py coa_analyzer.py \
         ofa_analyzer.py coa_main.py coa_boards.py delta_india.py config.py __main__.py \
         openalgo_client.py; do
  if [[ ! -f $SRC/$f ]]; then
    echo "missing $f" >&2
    exit 1
  fi
  install -m 0644 -o strikedesk -g strikedesk "$SRC/$f" "$DEST/$f"
done

python3 - <<'PY'
from pathlib import Path

path = Path("/opt/strike-desk/current/strike_desk/src/strike_desk/config.py")
text = path.read_text(encoding="utf-8")
changed = False
if "tt_auth_token_coa_mcx" not in text and "tt_auth_token_coa_btc" in text:
    needle = "    tt_auth_token_coa_btc: SecretStr | None = None\n"
    insert = needle + "    tt_auth_token_coa_mcx: SecretStr | None = None\n"
    if needle in text:
        text = text.replace(needle, insert, 1)
        changed = True
if "tt_auth_token_coa_btc" not in text:
    needle = "    tt_auth_token_coa: SecretStr | None = None\n"
    insert = (
        needle
        + "    tt_auth_token_coa_btc: SecretStr | None = None\n"
        + "    tt_auth_token_coa_mcx: SecretStr | None = None\n"
    )
    if needle not in text:
        needle = "    tt_auth_token_analyzer: SecretStr | None = None\n"
        insert = (
            needle
            + "    tt_auth_token_coa: SecretStr | None = None\n"
            + "    tt_auth_token_coa_btc: SecretStr | None = None\n"
        + "    tt_auth_token_coa_mcx: SecretStr | None = None\n"
            + "    coa_enabled: bool = False\n"
            + "    coa_interval_seconds: int = Field(default=300, ge=60, le=900)\n"
        )
    if needle not in text:
        raise SystemExit("cannot patch config.py: coa token field missing")
    text = text.replace(needle, insert, 1)
    changed = True
if "tt_auth_token_coa" not in text:
    needle = "    tt_auth_token_analyzer: SecretStr | None = None\n"
    insert = (
        needle
        + "    tt_auth_token_coa: SecretStr | None = None\n"
        + "    tt_auth_token_coa_btc: SecretStr | None = None\n"
        + "    tt_auth_token_coa_mcx: SecretStr | None = None\n"
        + "    coa_enabled: bool = False\n"
        + "    coa_interval_seconds: int = Field(default=300, ge=60, le=900)\n"
    )
    if needle not in text:
        raise SystemExit("cannot patch config.py: analyzer token field missing")
    text = text.replace(needle, insert, 1)
    changed = True
if "coa_lots" not in text:
    needle = "    coa_interval_seconds: int = Field(default=300, ge=60, le=900)\n"
    insert = (
        needle
        + "    coa_lots: int = Field(default=1, ge=1, le=20)\n"
        + '    coa_product: str = "MIS"\n'
    )
    if needle not in text:
        raise SystemExit("cannot patch config.py: coa interval field missing")
    text = text.replace(needle, insert, 1)
    changed = True
if "coa_heartbeat_path" not in text:
    needle = """    def ofa_heartbeat_path(self) -> Path:
        return self.state_dir / "ofa.heartbeat"
"""
    insert = needle + """
    @property
    def public_brief_path(self) -> Path:
        return self.state_dir / "public-brief.json"

    @property
    def coa_heartbeat_path(self) -> Path:
        return self.state_dir / "coa.heartbeat"
"""
    if needle not in text:
        raise SystemExit("cannot patch config.py: ofa heartbeat missing")
    if "def public_brief_path" in text:
        insert = needle + """
    @property
    def coa_heartbeat_path(self) -> Path:
        return self.state_dir / "coa.heartbeat"
"""
    text = text.replace(needle, insert, 1)
    changed = True
if changed:
    path.write_text(text, encoding="utf-8")
PY

python3 - <<PY
from pathlib import Path

env_path = Path("$ENV")
token = Path("$TOKEN_FILE").read_text(encoding="utf-8").replace("\r", "").replace("\n", "")
lines = env_path.read_text(encoding="utf-8").splitlines()
keys = {
    "STRIKE_DESK_TT_AUTH_TOKEN_COA": token,
    "STRIKE_DESK_COA_ENABLED": "true",
    "STRIKE_DESK_COA_INTERVAL_SECONDS": "60",
}
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in keys:
        out.append(f"{key}={keys[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in keys.items():
    if key not in seen:
        out.append(f"{key}={value}")
env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

BTC_TOKEN_FILE=$SRC/tt-coa-btc.token
if [[ -f $BTC_TOKEN_FILE ]]; then
python3 - <<PY
from pathlib import Path

env_path = Path("$ENV")
token = Path("$BTC_TOKEN_FILE").read_text(encoding="utf-8").replace("\r", "").replace("\n", "")
if not token:
    raise SystemExit("empty BTC CoA token")
lines = env_path.read_text(encoding="utf-8").splitlines()
key = "STRIKE_DESK_TT_AUTH_TOKEN_COA_BTC"
seen = False
out = []
for line in lines:
    name = line.split("=", 1)[0] if "=" in line else ""
    if name == key:
        out.append(f"{key}={token}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"{key}={token}")
env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
else
  echo "BTC CoA token file absent; runner will map-only (Hands idle)"
fi

NGINX_PATCHED=$(python3 - <<'PY'
from pathlib import Path

candidates = [
    Path("/etc/nginx/sites-enabled/desk.zerotouchalgo.com"),
    Path("/etc/nginx/sites-available/desk.zerotouchalgo.com"),
    Path("/etc/nginx/conf.d/desk.zerotouchalgo.com.conf"),
]
nginx = next((path for path in candidates if path.exists()), None)
if nginx is None:
    print("SKIP")
    raise SystemExit(0)
text = nginx.read_text(encoding="utf-8")
if "public-brief-btc-options.json" in text:
    print("SKIP")
    raise SystemExit(0)
block = """
    location = /public-brief-btc-options.json {
        alias /var/lib/strike-desk/public-brief-btc-options.json;
        default_type application/json;
        add_header Cache-Control "no-store";
        add_header Access-Control-Allow-Origin "https://zerotouchalgo.com";
        add_header Access-Control-Allow-Methods "GET, OPTIONS";
    }
"""
ssl_at = text.find("listen 443")
if ssl_at < 0:
    raise SystemExit("cannot patch nginx: no 443 server")
loc = text.find("    location / {", ssl_at)
if loc < 0:
    raise SystemExit("cannot patch nginx: ssl location / missing")
nginx.write_text(text[:loc] + block + "\n" + text[loc:], encoding="utf-8")
print("PATCHED")
PY
)
ALERT_PATCHED=$(python3 - <<'PY'
from pathlib import Path

candidates = [
    Path("/etc/nginx/sites-enabled/desk.zerotouchalgo.com"),
    Path("/etc/nginx/sites-available/desk.zerotouchalgo.com"),
    Path("/etc/nginx/conf.d/desk.zerotouchalgo.com.conf"),
]
nginx = next((path for path in candidates if path.exists()), None)
if nginx is None:
    print("SKIP")
    raise SystemExit(0)
text = nginx.read_text(encoding="utf-8")
if "location = /coa-alert" in text:
    print("SKIP")
    raise SystemExit(0)
block = """
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
ssl_at = text.find("listen 443")
if ssl_at < 0:
    raise SystemExit("cannot patch nginx: no 443 server")
loc = text.find("    location / {", ssl_at)
if loc < 0:
    raise SystemExit("cannot patch nginx: ssl location / missing")
nginx.write_text(text[:loc] + block + "\n" + text[loc:], encoding="utf-8")
print("PATCHED")
PY
)
if [[ $NGINX_PATCHED == PATCHED || $ALERT_PATCHED == PATCHED ]]; then
  nginx -t && systemctl reload nginx
fi

install -m 0644 "$UNIT_SRC" /etc/systemd/system/strike-desk-coa.service
systemctl daemon-reload
systemctl enable strike-desk-coa.service
# enable --now does not restart an already-active unit (Sunday overlay stayed on the Oct 22 PID).
OLD_PID=$(systemctl show -p MainPID --value strike-desk-coa.service)
systemctl restart strike-desk-coa.service
sleep 2
NEW_PID=$(systemctl show -p MainPID --value strike-desk-coa.service)
if [[ -z $NEW_PID || $NEW_PID == 0 ]]; then
  echo "strike-desk-coa.service has no MainPID after restart" >&2
  exit 1
fi
if [[ -n $OLD_PID && $OLD_PID != 0 && $OLD_PID == "$NEW_PID" ]]; then
  echo "strike-desk-coa.service PID unchanged after restart ($NEW_PID)" >&2
  exit 1
fi
systemctl is-active strike-desk-coa.service
systemctl is-active strike-desk.service
echo "coa_pid_before=$OLD_PID coa_pid_after=$NEW_PID"
systemctl show -p ActiveEnterTimestamp --value strike-desk-coa.service
test -f "$DEST/coa_analyzer.py"
echo INSTALL_OK
