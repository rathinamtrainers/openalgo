#!/usr/bin/env python3
import os
import pwd
from pathlib import Path

path = Path("/var/lib/strike-desk/coa_live_ng.json")
st = path.stat()
print("mode", oct(st.st_mode))
print("uid", st.st_uid, pwd.getpwuid(st.st_uid).pw_name)
print("gid", st.st_gid)
print("parent", oct(path.parent.stat().st_mode), pwd.getpwuid(path.parent.stat().st_uid).pw_name)
print("writable_as_strikedesk", os.access(path, os.W_OK))
