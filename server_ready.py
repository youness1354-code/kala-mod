import os
import re

# مسیر پوشه پروژه
BASE = os.path.dirname(os.path.abspath(__file__))

# فایل اصلی برنامه
APP_FILE = os.path.join(BASE, "kala_mod_fixed.py")

# بررسی وجود فایل برنامه
if not os.path.isfile(APP_FILE):
    raise FileNotFoundError(
        "فایل kala_mod_fixed.py در مخزن پیدا نشد."
    )

# خواندن کد برنامه
with open(APP_FILE, "r", encoding="utf-8") as f:
    code = f.read()

# تنظیم مسیر ذخیره اطلاعات برای سرور
code = re.sub(
    r"(?m)^BASE_DIR\s*=\s*.*$",
    'BASE_DIR = os.environ.get("KALAMOD_DATA_DIR", os.path.join(BASE, "data"))',
    code,
    count=1,
)

# تنظیم کلید امنیتی برنامه
code = re.sub(
    r"(?m)^app\.secret_key\s*=\s*.*$",
    'app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)',
    code,
    count=1,
)

# اجرای برنامه در فضای نام همین فایل؛
# این کار باعث می‌شود Gunicorn بتواند app را پیدا کند.
exec(compile(code, APP_FILE, "exec"), globals())
