#!/usr/bin/env python3
"""Add ltp.zerotouchalgo.com to the desk/lab nginx vhost and expand the cert.

Reloads nginx. Restarts openalgo only if CORS is patched.
Does not restart strike-desk or CoA.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

NGINX = Path("/etc/nginx/sites-available/desk.zerotouchalgo.com")
ENABLED = Path("/etc/nginx/sites-enabled/desk.zerotouchalgo.com")
ENV = Path("/opt/openalgo/current/.env")
SERVER = "server_name desk.zerotouchalgo.com lab.zerotouchalgo.com ltp.zerotouchalgo.com;"
LTP_REWRITE = """    if ($host = ltp.zerotouchalgo.com) {
        rewrite ^/$ /greekslab redirect;
    }
"""


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode != 0:
        raise SystemExit(f"cmd failed {p.returncode}: {' '.join(cmd)}\n{out[:800]}")
    return out


def patch_nginx() -> None:
    text = NGINX.read_text(encoding="utf-8")
    if "ltp.zerotouchalgo.com" not in text.split("server_name", 1)[-1][:200]:
        text = text.replace(
            "server_name desk.zerotouchalgo.com lab.zerotouchalgo.com;",
            SERVER,
        )
        text = text.replace(
            "server_name desk.zerotouchalgo.com;",
            SERVER,
        )
    if "ltp.zerotouchalgo.com) {" not in text:
        needle = """    if ($host = lab.zerotouchalgo.com) {
        rewrite ^/$ /greekslab redirect;
    }
"""
        if needle not in text:
            raise SystemExit("cannot insert ltp rewrite")
        text = text.replace(needle, needle + LTP_REWRITE, 1)
    NGINX.write_text(text, encoding="utf-8")
    if ENABLED.exists() and ENABLED.resolve() != NGINX.resolve():
        ENABLED.write_text(text, encoding="utf-8")
    print("patched nginx")
    print(run(["nginx", "-t"]))
    run(["systemctl", "reload", "nginx"])
    print("nginx reloaded")


def expand_cert() -> None:
    out = run(
        [
            "certbot",
            "certonly",
            "--nginx",
            "--non-interactive",
            "--agree-tos",
            "--keep-until-expiring",
            "--expand",
            "-d",
            "desk.zerotouchalgo.com",
            "-d",
            "lab.zerotouchalgo.com",
            "-d",
            "ltp.zerotouchalgo.com",
        ]
    )
    print(out[-500:] if len(out) > 500 else out)
    run(["nginx", "-t"])
    run(["systemctl", "reload", "nginx"])
    print("cert expanded")


def patch_cors() -> bool:
    if not ENV.exists():
        print("no .env; skip CORS")
        return False
    text = ENV.read_text(encoding="utf-8")
    if "ltp.zerotouchalgo.com" in text:
        print("CORS already has ltp")
        return False
    old = "CORS_ALLOWED_ORIGINS = 'https://desk.zerotouchalgo.com,https://lab.zerotouchalgo.com'"
    new = (
        "CORS_ALLOWED_ORIGINS = "
        "'https://desk.zerotouchalgo.com,https://lab.zerotouchalgo.com,https://ltp.zerotouchalgo.com'"
    )
    if old not in text:
        print("CORS line not in expected shape; skip")
        return False
    ENV.write_text(text.replace(old, new, 1), encoding="utf-8")
    ENV.chmod(0o600)
    print("patched CORS for ltp")
    return True


def main() -> None:
    patch_nginx()
    expand_cert()
    if patch_cors():
        run(["systemctl", "restart", "openalgo.service"])
        print("openalgo", run(["systemctl", "is-active", "openalgo.service"]))
    print("coa", run(["systemctl", "is-active", "strike-desk-coa.service"]))
    print("desk", run(["systemctl", "is-active", "strike-desk.service"]))
    print("LTP_HOST_OK")


if __name__ == "__main__":
    main()
