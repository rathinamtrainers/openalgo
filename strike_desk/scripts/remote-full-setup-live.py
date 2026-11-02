#!/usr/bin/env python3
"""Confirm desk/lab/ltp serve the new menu bundle. No secrets."""

from __future__ import annotations

import urllib.request

UA = {"User-Agent": "strike-desk-full-setup-check/1"}
for url in (
    "https://desk.zerotouchalgo.com/",
    "https://lab.zerotouchalgo.com/",
    "https://ltp.zerotouchalgo.com/",
):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8", errors="ignore")
            status = response.status
            final = response.geturl()
    except Exception as exc:
        print(url, "ERR", type(exc).__name__)
        continue
    print(
        url,
        "http",
        status,
        "final",
        final,
        "index_U8z",
        "index-U8zNmwNd.js" in html,
        "greekslab",
        "/greekslab" in html or "greekslab" in final,
    )
