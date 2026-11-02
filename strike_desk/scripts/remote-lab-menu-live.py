#!/usr/bin/env python3
"""Confirm lab.zerotouchalgo.com HTML references the new menu bundle."""

from __future__ import annotations

import urllib.request

req = urllib.request.Request(
    "https://lab.zerotouchalgo.com/",
    headers={"User-Agent": "strike-desk-lab-menu-check/1"},
)
with urllib.request.urlopen(req, timeout=15) as response:
    status = response.status
    html = response.read().decode("utf-8", errors="ignore")
print("http", status)
for token in ("index-U8zNmwNd.js", "GreeksLab-DJdW0TdR.js", "lab.zerotouchalgo.com"):
    print(token, token in html)
for part in html.split('"'):
    if part.startswith("assets/index-") and part.endswith(".js"):
        print("html_index", part)
