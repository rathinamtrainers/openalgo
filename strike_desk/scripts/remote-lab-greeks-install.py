"""Install Greeks Lab onto GCE OpenAlgo and add lab.zerotouchalgo.com.

Restarts openalgo.service only. Does not restart strike-desk or CoA.
Never prints .env secrets.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

SRC = Path("/tmp/sd-lab")
OA = Path("/opt/openalgo/current")
NGINX = Path("/etc/nginx/sites-available/desk.zerotouchalgo.com")
ENV = OA / ".env"


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _patch_app() -> None:
    path = OA / "app.py"
    text = path.read_text(encoding="utf-8")
    changed = False
    if "greeks_lab_bp" not in text:
        needle = "from blueprints.gex import gex_bp  # Import the GEX blueprint\n"
        insert = needle + "from blueprints.greeks_lab import greeks_lab_bp  # Import the Greeks Lab blueprint\n"
        if needle not in text:
            raise SystemExit("cannot patch app.py import")
        text = text.replace(needle, insert, 1)
        changed = True
    if "register_blueprint(greeks_lab_bp)" not in text:
        needle = "    app.register_blueprint(gex_bp)  # Register GEX blueprint\n"
        insert = needle + "    app.register_blueprint(greeks_lab_bp)  # Register Greeks Lab blueprint\n"
        if needle not in text:
            raise SystemExit("cannot patch app.py register")
        text = text.replace(needle, insert, 1)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
        print("patched", path)


def _patch_react() -> None:
    path = OA / "blueprints" / "react_app.py"
    text = path.read_text(encoding="utf-8")
    if 'route("/greekslab")' in text:
        print("react_app already has /greekslab")
        return
    needle = '''@react_bp.route("/gex")
def react_gex():
    return serve_react_app()
'''
    insert = needle + '''

@react_bp.route("/greekslab")
def react_greekslab():
    return serve_react_app()
'''
    if needle not in text:
        raise SystemExit("cannot patch react_app.py")
    path.write_text(text.replace(needle, insert, 1), encoding="utf-8")
    print("patched", path)


def _copy_backend() -> None:
    dest_svc = OA / "services" / "greeks_lab_service.py"
    dest_ins = OA / "services" / "greeks_lab_insights.py"
    dest_coa = OA / "services" / "greeks_lab_coa.py"
    dest_nar = OA / "services" / "greeks_lab_narrate.py"
    dest_chain = OA / "services" / "option_chain_service.py"
    dest_sym = OA / "services" / "option_symbol_service.py"
    dest_bp = OA / "blueprints" / "greeks_lab.py"
    shutil.copyfile(SRC / "greeks_lab_service.py", dest_svc)
    shutil.copyfile(SRC / "greeks_lab_insights.py", dest_ins)
    shutil.copyfile(SRC / "greeks_lab_coa.py", dest_coa)
    shutil.copyfile(SRC / "greeks_lab_narrate.py", dest_nar)
    shutil.copyfile(SRC / "option_chain_service.py", dest_chain)
    shutil.copyfile(SRC / "option_symbol_service.py", dest_sym)
    shutil.copyfile(SRC / "greeks_lab.py", dest_bp)
    for path in (dest_svc, dest_ins, dest_coa, dest_nar, dest_chain, dest_sym, dest_bp):
        path.chmod(0o644)
    print("wrote", dest_svc, dest_ins, dest_coa, dest_nar, dest_chain, dest_sym, dest_bp)


def _copy_dist() -> None:
    src_dist = SRC / "dist"
    dest_dist = OA / "frontend" / "dist"
    if not src_dist.exists():
        raise SystemExit("missing frontend dist")
    if dest_dist.exists():
        shutil.rmtree(dest_dist)
    shutil.copytree(src_dist, dest_dist)
    print("wrote dist", dest_dist)


def _chown() -> None:
    _run(["chown", "-R", "openalgo:openalgo", str(OA / "services" / "greeks_lab_service.py")])
    _run(["chown", "-R", "openalgo:openalgo", str(OA / "services" / "greeks_lab_insights.py")])
    _run(["chown", "-R", "openalgo:openalgo", str(OA / "services" / "greeks_lab_coa.py")])
    _run(["chown", "-R", "openalgo:openalgo", str(OA / "services" / "greeks_lab_narrate.py")])
    _run(["chown", "-R", "openalgo:openalgo", str(OA / "services" / "option_chain_service.py")])
    _run(["chown", "-R", "openalgo:openalgo", str(OA / "services" / "option_symbol_service.py")])
    _run(["chown", "-R", "openalgo:openalgo", str(OA / "blueprints" / "greeks_lab.py")])
    _run(["chown", "openalgo:openalgo", str(OA / "app.py"), str(OA / "blueprints" / "react_app.py")])
    _run(["chown", "-R", "openalgo:openalgo", str(OA / "frontend" / "dist")])


def _patch_cors() -> None:
    if not ENV.exists():
        print("no .env; skip CORS")
        return
    text = ENV.read_text(encoding="utf-8")
    if "lab.zerotouchalgo.com" in text:
        print("CORS already has lab")
        return
    old = "CORS_ALLOWED_ORIGINS = 'https://desk.zerotouchalgo.com'"
    new = (
        "CORS_ALLOWED_ORIGINS = "
        "'https://desk.zerotouchalgo.com,https://lab.zerotouchalgo.com,https://ltp.zerotouchalgo.com'"
    )
    if old not in text and "lab.zerotouchalgo.com'" in text and "ltp.zerotouchalgo.com" not in text:
        old = "CORS_ALLOWED_ORIGINS = 'https://desk.zerotouchalgo.com,https://lab.zerotouchalgo.com'"
        new = (
            "CORS_ALLOWED_ORIGINS = "
            "'https://desk.zerotouchalgo.com,https://lab.zerotouchalgo.com,https://ltp.zerotouchalgo.com'"
        )
    if old not in text:
        print("CORS line not in expected shape; skip")
        return
    ENV.write_text(text.replace(old, new, 1), encoding="utf-8")
    ENV.chmod(0o600)
    print("patched CORS for lab")


def _patch_nginx() -> None:
    if not NGINX.exists():
        raise SystemExit("missing nginx vhost")
    text = NGINX.read_text(encoding="utf-8")
    if "lab.zerotouchalgo.com" not in text:
        text = text.replace(
            "server_name desk.zerotouchalgo.com;",
            "server_name desk.zerotouchalgo.com lab.zerotouchalgo.com ltp.zerotouchalgo.com;",
        )
    if "ltp.zerotouchalgo.com" not in text:
        text = text.replace(
            "server_name desk.zerotouchalgo.com lab.zerotouchalgo.com;",
            "server_name desk.zerotouchalgo.com lab.zerotouchalgo.com ltp.zerotouchalgo.com;",
        )
    rewrite = """
    if ($host = lab.zerotouchalgo.com) {
        rewrite ^/$ /greekslab redirect;
    }
    if ($host = ltp.zerotouchalgo.com) {
        rewrite ^/$ /greekslab redirect;
    }
"""
    if "ltp.zerotouchalgo.com) {" not in text and "rewrite ^/$ /greekslab" in text:
        text = text.replace(
            """    if ($host = lab.zerotouchalgo.com) {
        rewrite ^/$ /greekslab redirect;
    }
""",
            rewrite,
            1,
        )
    if "rewrite ^/$ /greekslab" not in text:
        marker = "    location = /ws {"
        if marker not in text:
            raise SystemExit("cannot insert lab rewrite")
        text = text.replace(marker, rewrite + "\n" + marker, 1)
    NGINX.write_text(text, encoding="utf-8")
    enabled = Path("/etc/nginx/sites-enabled/desk.zerotouchalgo.com")
    if enabled.is_symlink() or enabled.exists():
        if enabled.resolve() != NGINX.resolve():
            enabled.write_text(text, encoding="utf-8")
    print("patched nginx vhost")
    _run(["nginx", "-t"])
    _run(["systemctl", "reload", "nginx"])


def main() -> None:
    _copy_backend()
    _patch_app()
    _patch_react()
    _copy_dist()
    _patch_cors()
    _patch_nginx()
    _chown()
    _run(["systemctl", "restart", "openalgo.service"])
    _run(["systemctl", "is-active", "openalgo.service"])
    _run(["systemctl", "is-active", "strike-desk-coa.service"])
    print("lab greeks install ok")


if __name__ == "__main__":
    main()
