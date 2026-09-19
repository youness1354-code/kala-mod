import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE, "app.py"), "r", encoding="utf-8") as f:
    code = f.read()

code = code.replace(
    'BASE_DIR = "/storage/emulated/0/kalamod"',
    'BASE_DIR = os.environ.get("KALAMOD_DATA_DIR", os.path.join(BASE, "data"))'
)

code = re.sub(
    r'app\.secret_key\s*=\s*.*',
    'app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)',
    code,
    count=1
)

exec(compile(code, "app.py", "exec"))
