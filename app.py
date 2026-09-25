from flask import Flask, request, render_template_string, send_from_directory, redirect, url_for, session, abort
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import json
import uuid
import re
import zipfile
from datetime import datetime, timedelta

app = Flask(__name__)
BASE_DIR = "/tmp/kalamod"
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
FILES = {
    "listings": os.path.join(BASE_DIR, "listings.json"),
    "messages": os.path.join(BASE_DIR, "messages.json"),
    "favorites": os.path.join(BASE_DIR, "favorites.json"),
    "reports": os.path.join(BASE_DIR, "reports.json"),
    "users": os.path.join(BASE_DIR, "users.json"),
    "notifications": os.path.join(BASE_DIR, "notifications.json"),
    "ratings": os.path.join(BASE_DIR, "ratings.json"),
    "blocks": os.path.join(BASE_DIR, "blocks.json"),
}
USERS_FILE = FILES["users"]
DATA_FILE = FILES["listings"]
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Kalamod@2026"
app.secret_key = "kalamod-local-secret-change-later"
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

CATEGORIES = ["همه", "موبایل", "لوازم دیجیتال", "خودرو", "املاک", "لوازم خانه", "پوشاک", "ورزش", "کتاب", "سایر"]
EXT = {"jpg", "jpeg", "png", "webp", "gif"}
BAD = ["فحش", "احمق", "بی شعور", "بی‌شعور", "کثافت"]
PROMOTE_DAYS = 3
LADDER_DAYS = 1
LISTING_DAYS = 30


def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


listings = load_json(FILES["listings"], [])
messages = load_json(FILES["messages"], {})
favorites = load_json(FILES["favorites"], {})
reports = load_json(FILES["reports"], [])
users = load_json(FILES["users"], [])
notifications = load_json(FILES["notifications"], [])
ratings = load_json(FILES["ratings"], [])
blocks = load_json(FILES["blocks"], {})
if not isinstance(ratings, list):
    ratings = []
    save_json(FILES["ratings"], ratings)
if not isinstance(blocks, dict):
    blocks = {}
    save_json(FILES["blocks"], blocks)

if not isinstance(listings, list):
    listings = []
if not isinstance(messages, dict):
    messages = {}
if isinstance(favorites, list):
    favorites = {"legacy": favorites}
if not isinstance(favorites, dict):
    favorites = {}
if not isinstance(reports, list):
    reports = []
if not isinstance(users, list):
    users = []
if not isinstance(notifications, list):
    notifications = []


def now_text():
    return datetime.now().strftime("%Y/%m/%d - %H:%M")


def iso_now():
    return datetime.now().isoformat(timespec="seconds")


def iso_after(days):
    return (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")


def parse_iso(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def allowed_file(filename):
    return bool(filename and "." in filename and filename.rsplit(".", 1)[1].lower() in EXT)


def contains_bad_word(text):
    text = str(text or "").lower()
    return any(word.lower() in text for word in BAD)


def price_number(value):
    raw = str(value or "").replace(",", "").replace("٬", "").replace("تومان", "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else 0


def format_price(value):
    n = price_number(value)
    return f"{n:,} تومان" if n else ("توافقی" if not str(value or "").strip() else str(value))


def owner_of(item):
    return get_user_by_id(item.get("owner_id"))


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return next((u for u in users if str(u.get("id")) == str(uid)), None)


def get_user_by_id(uid):
    return next((u for u in users if str(u.get("id")) == str(uid)), None)


def get_user_by_username(username):
    n = str(username or "").strip().lower()
    return next((u for u in users if str(u.get("username", "")).strip().lower() == n), None)


def is_admin(user=None):
    user = user if user is not None else current_user()
    return bool(user and str(user.get("role", "")).lower() == "admin")


def admin_user():
    return next((u for u in users if is_admin(u)), None)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if not is_admin(user):
            return "دسترسی غیرمجاز", 403
        return fn(*args, **kwargs)
    return wrapper


def is_blocked(user_a, user_b):
    """True if either user has blocked the other."""
    a, b = str(user_a), str(user_b)
    return b in {str(x) for x in blocks.get(a, [])} or a in {str(x) for x in blocks.get(b, [])}


def add_block(user_id, blocked_id):
    a, b = str(user_id), str(blocked_id)
    if a == b:
        return
    arr = blocks.setdefault(a, [])
    if b not in [str(x) for x in arr]:
        arr.append(b)
    save_json(FILES["blocks"], blocks)


def remove_block(user_id, blocked_id):
    a, b = str(user_id), str(blocked_id)
    blocks[a] = [x for x in blocks.get(a, []) if str(x) != b]
    save_json(FILES["blocks"], blocks)


def safe_next(value):
    value = str(value or "/")
    return value if value.startswith("/") and not value.startswith("//") else "/"


def find_listing(listing_id):
    for item in listings:
        try:
            if int(item.get("id")) == int(listing_id):
                return item
        except Exception:
            continue
    return None


def next_listing_id():
    nums = []
    for item in listings:
        try:
            nums.append(int(item.get("id")))
        except Exception:
            pass
    return max(nums, default=0) + 1


def normalize_favorites():
    changed = False
    for uid, ids in list(favorites.items()):
        if not isinstance(ids, list):
            favorites[uid] = []
            changed = True
            continue
        new_ids = []
        for item_id in ids:
            try:
                n = int(item_id)
                if n not in new_ids:
                    new_ids.append(n)
            except Exception:
                changed = True
        if new_ids != ids:
            favorites[uid] = new_ids
            changed = True
    if changed:
        save_json(FILES["favorites"], favorites)


def ensure_data():
    global notifications
    changed_users = False
    changed_listings = False
    changed_messages = False
    changed_notifications = False

    admin = get_user_by_username(ADMIN_USERNAME)
    if not admin:
        users.append({
            "id": uuid.uuid4().hex,
            "username": ADMIN_USERNAME,
            "password": generate_password_hash(ADMIN_PASSWORD),
            "created_at": now_text(),
            "role": "admin",
            "disabled": False,
        })
        changed_users = True

    for user in users:
        if "role" not in user:
            user["role"] = "user"
            changed_users = True
        if "disabled" not in user:
            user["disabled"] = False
            changed_users = True

    used_ids = set()
    for item in listings:
        try:
            lid = int(item.get("id"))
        except Exception:
            lid = None
        if lid is None or lid in used_ids:
            lid = max(used_ids, default=0) + 1
            item["id"] = lid
            changed_listings = True
        used_ids.add(lid)
        if not item.get("owner_id"):
            item["owner_id"] = "legacy"
            changed_listings = True
        if not item.get("owner_name"):
            item["owner_name"] = "کاربر قدیمی"
            changed_listings = True
        if "active" not in item:
            item["active"] = True
            changed_listings = True
        if "featured_until" not in item:
            item["featured_until"] = ""
            changed_listings = True
        if "last_ladder_at" not in item:
            item["last_ladder_at"] = ""
            changed_listings = True
        if "views" not in item:
            item["views"] = 0
            changed_listings = True
        if item.get("status") not in {"active", "sold", "hidden"}:
            item["status"] = "active"
            changed_listings = True

    for key, msg_list in list(messages.items()):
        if not isinstance(msg_list, list):
            messages[key] = []
            changed_messages = True
            continue
        fixed = []
        for msg in msg_list:
            if isinstance(msg, str):
                msg = {"id": uuid.uuid4().hex, "user_id": "legacy", "username": "کاربر", "role": "کاربر", "text": msg, "time": ""}
                changed_messages = True
            elif isinstance(msg, dict):
                msg = dict(msg)
                if not msg.get("id"):
                    msg["id"] = uuid.uuid4().hex
                    changed_messages = True
            else:
                changed_messages = True
                continue
            fixed.append(msg)
        messages[key] = fixed

    if not isinstance(notifications, list):
        notifications = []
        changed_notifications = True

    if changed_users:
        save_json(FILES["users"], users)
    if changed_listings:
        save_json(FILES["listings"], listings)
    if changed_messages:
        save_json(FILES["messages"], messages)
    if changed_notifications:
        save_json(FILES["notifications"], notifications)
    normalize_favorites()


ensure_data()


def create_notification(user_id, title, text, target="/notifications"):
    if not user_id or str(user_id) == "legacy":
        return
    notifications.append({
        "id": uuid.uuid4().hex,
        "user_id": str(user_id),
        "title": str(title),
        "text": str(text),
        "target": target,
        "read": False,
        "created_at": now_text(),
    })
    save_json(FILES["notifications"], notifications)


def unread_count(user_id):
    return sum(1 for n in notifications if str(n.get("user_id")) == str(user_id) and not n.get("read", False))


def active_for_public(item):
    if not bool(item.get("active", True)) or item.get("status", "active") != "active":
        return False
    expires = parse_iso(item.get("expires_at", ""))
    return not expires or expires > datetime.now()


def ensure_listing_expirations():
    changed = False
    for item in listings:
        if not item.get("expires_at") and item.get("created_at"):
            try:
                created = datetime.strptime(str(item.get("created_at")), "%Y/%m/%d - %H:%M")
                item["expires_at"] = (created + timedelta(days=LISTING_DAYS)).isoformat(timespec="seconds")
                changed = True
            except Exception:
                item["expires_at"] = iso_after(LISTING_DAYS)
                changed = True
        if item.get("status") == "active" and item.get("expires_at"):
            expires = parse_iso(item.get("expires_at"))
            if expires and expires <= datetime.now() and item.get("active", True):
                item["active"] = False
                item["status"] = "expired"
                item["featured_until"] = ""
                changed = True
    if changed:
        save_json(FILES["listings"], listings)


def featured_active(item):
    until = parse_iso(item.get("featured_until", ""))
    return bool(until and until > datetime.now() and item.get("active", True))


def expire_promotions():
    changed = False
    for item in listings:
        until = parse_iso(item.get("featured_until", ""))
        if item.get("featured_until") and (until is None or until <= datetime.now()):
            item["featured_until"] = ""
            changed = True
    if changed:
        save_json(FILES["listings"], listings)


expire_promotions()
ensure_listing_expirations()

STYLE = """<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#222;font-family:Tahoma,Arial,sans-serif}a{text-decoration:none;color:inherit}
.head{background:#111827;color:#fff;padding:14px;position:sticky;top:0;z-index:10}.headin{max-width:1050px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:10px}.logo{font-size:23px;font-weight:bold}.nav{display:flex;gap:6px;flex-wrap:wrap}.nav a{background:#263244;padding:8px 10px;border-radius:9px;font-size:13px}
.wrap{max-width:1050px;margin:20px auto;padding:0 12px}.card{background:#fff;border-radius:16px;padding:16px;margin-bottom:15px;box-shadow:0 4px 18px #0000000d}input,select,textarea{width:100%;padding:12px;margin:6px 0 12px;border:1px solid #ddd;border-radius:10px;font:inherit}textarea{min-height:110px}button,.btn{border:0;background:#111827;color:white;padding:11px 15px;border-radius:10px;display:inline-block;font:inherit;cursor:pointer}.blue{background:#2563eb}.green{background:#15803d}.red{background:#b91c1c}.light{background:#eef2f7;color:#111827}.orange{background:#b45309}.purple{background:#7c3aed}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(245px,1fr));gap:15px}.item{background:#fff;border-radius:15px;overflow:hidden;box-shadow:0 4px 15px #0000000d}.pic{width:100%;height:190px;object-fit:cover;background:#e5e7eb}.nop{height:190px;display:flex;align-items:center;justify-content:center;background:#e5e7eb;color:#666}.body{padding:14px}.price{font-weight:bold;font-size:17px;margin:8px 0}.meta,.small{color:#6b7280;font-size:13px;line-height:1.9}.box{background:#f8fafc;border:1px solid #e5e7eb;padding:14px;border-radius:13px;margin-top:15px}.avatar{width:62px;height:62px;border-radius:50%;background:#111827;color:#fff;display:flex;align-items:center;justify-content:center;font-size:25px;font-weight:bold}.profile{display:flex;gap:15px;align-items:center}.center{text-align:center}.err{background:#fee2e2;color:#991b1b;padding:12px;border-radius:10px;margin-bottom:12px}.ok{background:#dcfce7;color:#166534;padding:12px;border-radius:10px}.msg{padding:10px 13px;border-radius:12px;margin-bottom:9px;max-width:80%}.me{background:#dbeafe;margin-right:auto}.other{background:#f3f4f6;margin-left:auto}.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#fef3c7;color:#92400e;font-size:11px}.statgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.stat{background:#f8fafc;border-radius:12px;padding:12px;text-align:center}.stat b{font-size:20px;display:block;margin-bottom:4px}.filters{display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr;gap:8px}.hero{background:linear-gradient(135deg,#111827,#374151);color:white;border-radius:18px;padding:22px;margin-bottom:15px}.featured{border:2px solid #f59e0b}.mini{font-size:12px;color:#6b7280}@media(max-width:750px){.headin{flex-direction:column;align-items:flex-start}.filters{grid-template-columns:1fr 1fr}.statgrid{grid-template-columns:1fr 1fr}}
</style>"""


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = uuid.uuid4().hex + uuid.uuid4().hex
        session["csrf_token"] = token
    return token


def inject_csrf(html):
    token = csrf_token()
    # Add a token to every normal POST form rendered by this app.
    return re.sub(r"(<form\b(?=[^>]*\bmethod=[\"\']post[\"\'])[^>]*>)", lambda m: m.group(1) + f"<input type=\"hidden\" name=\"csrf_token\" value=\"{token}\">", html, flags=re.I)


def check_csrf():
    if request.method == "POST":
        expected = session.get("csrf_token")
        supplied = request.form.get("csrf_token", "")
        if not expected or not supplied or supplied != expected:
            abort(400, description="درخواست نامعتبر است. صفحه را تازه‌سازی کنید و دوباره تلاش کنید.")


@app.before_request
def protect_posts():
    check_csrf()


def page(title, body, **ctx):
    if "user" not in ctx:
        ctx["user"] = current_user()
    if "unread" not in ctx:
        user = ctx.get("user")
        ctx["unread"] = unread_count(user.get("id")) if user else 0
    html = """<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{{title}}</title>""" + STYLE + """</head><body>
<div class='head'><div class='headin'><a class='logo' href='{{url_for("home")}}'>کالا مود</a><div class='nav'><a href='{{url_for("home")}}'>خانه</a>{% if user %}<a href='{{url_for("favorites_page")}}'>❤️ علاقه‌مندی‌ها</a><a href='{{url_for("inbox")}}'>💬 پیام‌ها</a><a href='{{url_for("notifications_page")}}'>🔔 اعلان{% if unread %} ({{unread}}){% endif %}</a><a href='{{url_for("profile")}}'>👤 {{user.username}}</a>{% if user.role == "admin" %}<a href='{{url_for("admin_panel")}}'>🛠 مدیریت</a>{% endif %}<a href='{{url_for("logout")}}'>خروج</a>{% else %}<a href='{{url_for("login")}}'>ورود</a><a href='{{url_for("register")}}'>ثبت‌نام</a>{% endif %}<a href='{{url_for("support")}}'>پشتیبانی</a></div></div></div>
<div class='wrap'>""" + body + """</div></body></html>"""
    rendered = render_template_string(html, title=title, **ctx)
    return inject_csrf(rendered)


CARD = """<div class='item {% if featured_active(item) %}featured{% endif %}'>{% if item.get('image') %}<img class='pic' src='{{url_for("uploaded_file",filename=item.image)}}'>{% else %}<div class='nop'>بدون تصویر</div>{% endif %}<div class='body'>
{% if featured_active(item) %}<span class='badge'>⭐ آگهی ویژه</span>{% endif %}{% if item.status=='sold' %}<span class='badge'>✅ فروخته‌شده</span>{% endif %}<h3>{{item.get('title','بدون عنوان')}}</h3><div class='price'>{{format_price(item.get('price','توافقی'))}}</div><div class='meta'>📁 {{item.get('category','سایر')}}<br>👤 <a style='color:#2563eb' href='{{url_for("seller_page",seller_id=(item.get("owner_id") or "legacy"))}}'>{{item.get('owner_name','کاربر قدیمی')}}</a></div><div class='actions'><a class='btn' href='{{url_for("listing_detail",listing_id=item.id)}}'>مشاهده</a>{% if user %}<a class='btn light' href='{{url_for("toggle_favorite",listing_id=item.id)}}'>{% if item.id in user_favs %}💔{% else %}❤️{% endif %}</a>{% endif %}</div></div></div>"""


@app.context_processor
def inject_helpers():
    return {"featured_active": featured_active, "format_price": format_price}



def rating_for_seller(seller_id):
    rows = [r for r in ratings if isinstance(r, dict) and str(r.get("seller_id")) == str(seller_id)]
    if not rows:
        return {"average": 0, "count": 0}
    vals = [int(r.get("score", 0)) for r in rows if str(r.get("score", "")).isdigit() and 1 <= int(r.get("score", 0)) <= 5]
    return {"average": round(sum(vals) / len(vals), 1) if vals else 0, "count": len(vals)}

def user_rated_listing(user_id, listing_id):
    return any(isinstance(r, dict) and str(r.get("user_id")) == str(user_id) and str(r.get("listing_id")) == str(listing_id) for r in ratings)

@app.route("/")
def home():
    expire_promotions()
    user = current_user()
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "همه")
    sort = request.args.get("sort", "newest")
    min_price = request.args.get("min_price", "").strip()
    max_price = request.args.get("max_price", "").strip()

    def price_num(item):
        return price_number(item.get("price", ""))

    min_n = int(min_price.replace(",", "").replace("٬", "")) if min_price.replace(",", "").replace("٬", "").isdigit() else None
    max_n = int(max_price.replace(",", "").replace("٬", "")) if max_price.replace(",", "").replace("٬", "").isdigit() else None
    result = []
    for item in listings:
        if not active_for_public(item) and not is_admin(user):
            continue
        text = (str(item.get("title", "")) + " " + str(item.get("description", ""))).lower()
        if q and q.lower() not in text:
            continue
        if cat != "همه" and item.get("category") != cat:
            continue
        p = price_num(item)
        if min_n is not None and p and p < min_n:
            continue
        if max_n is not None and p and p > max_n:
            continue
        result.append(item)

    if sort == "cheapest":
        result.sort(key=price_num)
    elif sort == "expensive":
        result.sort(key=price_num, reverse=True)
    elif sort == "popular":
        result.sort(key=lambda x: int(x.get("views", 0) or 0), reverse=True)
    elif sort == "featured":
        result.sort(key=lambda x: (featured_active(x), x.get("created_at", "")), reverse=True)
    else:
        result.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    featured = [x for x in listings if active_for_public(x) and featured_active(x)]
    featured.sort(key=lambda x: x.get("featured_until", ""), reverse=True)
    newest = [x for x in listings if active_for_public(x)]
    newest.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    popular = [x for x in listings if active_for_public(x)]
    popular.sort(key=lambda x: int(x.get("views", 0) or 0), reverse=True)
    favs = favorites.get(str(user.get("id")), favorites.get(user.get("id"), [])) if user else []
    if not isinstance(favs, list):
        favs = []
    body = """<div class='hero'><h1>کالا مود</h1><p>بازار آنلاین خرید و فروش کالا</p></div>
<div class='card'><form method='get'><div class='filters'><input name='q' value='{{q}}' placeholder='جستجوی عنوان یا توضیحات'><select name='category'>{% for c in categories %}<option value='{{c}}' {% if c==cat %}selected{% endif %}>{{c}}</option>{% endfor %}</select><input name='min_price' value='{{min_price}}' inputmode='numeric' placeholder='حداقل قیمت'><input name='max_price' value='{{max_price}}' inputmode='numeric' placeholder='حداکثر قیمت'><select name='sort'><option value='newest' {% if sort=='newest' %}selected{% endif %}>جدیدترین</option><option value='cheapest' {% if sort=='cheapest' %}selected{% endif %}>ارزان‌ترین</option><option value='expensive' {% if sort=='expensive' %}selected{% endif %}>گران‌ترین</option><option value='popular' {% if sort=='popular' %}selected{% endif %}>پربازدیدترین</option><option value='featured' {% if sort=='featured' %}selected{% endif %}>آگهی‌های ویژه</option></select></div><button class='blue'>🔎 جستجو و فیلتر</button></form></div>
{% if user %}<div class='card'><h3>➕ ثبت آگهی جدید</h3><form method='post' action='{{url_for("add_listing")}}' enctype='multipart/form-data'><label>عنوان</label><input name='title' maxlength='100' required><label>دسته‌بندی</label><select name='category'>{% for c in categories if c!='همه' %}<option>{{c}}</option>{% endfor %}</select><label>قیمت</label><input name='price' maxlength='30' placeholder='مثلاً 25000000 یا توافقی'><label>توضیحات</label><textarea name='description' maxlength='2000'></textarea><label>تصویر (حداکثر ۸ مگابایت)</label><input type='file' name='image' accept='.jpg,.jpeg,.png,.webp,.gif'><button class='blue'>ثبت آگهی</button></form></div>{% else %}<div class='card center'><h3>برای ثبت آگهی وارد حساب شو</h3><a class='btn blue' href='{{url_for("login")}}'>ورود</a> <a class='btn light' href='{{url_for("register")}}'>ثبت‌نام</a></div>{% endif %}
{% if featured %}<div class='card'><h2>⭐ آگهی‌های ویژه</h2><div class='grid'>{% for item in featured[:8] %}""" + CARD + """{% endfor %}</div></div>{% endif %}
<div class='card'><h2>📦 آگهی‌ها <span class='mini'>({{result|length}} مورد)</span></h2></div><div class='grid'>{% for item in result %}""" + CARD + """{% else %}<div class='card'>هیچ آگهی‌ای با این فیلتر پیدا نشد.</div>{% endfor %}</div>
{% if newest %}<div class='card'><h2>🆕 تازه‌ترین‌ها</h2><div class='grid'>{% for item in newest[:4] %}""" + CARD + """{% endfor %}</div></div>{% endif %}"""
    body += """<div class='card'><h2>🔥 پربازدیدترین‌ها</h2><div class='grid'>{% for item in popular %}""" + CARD + """{% endfor %}</div></div>"""
    return page("کالا مود", body, user=user, result=result, categories=CATEGORIES, q=q, cat=cat, sort=sort, min_price=min_price, max_price=max_price, user_favs=favs, featured=featured, newest=newest, popular=popular)


@app.route("/add", methods=["POST"])
@login_required
def add_listing():
    user = current_user()
    title = request.form.get("title", "").strip()
    category = request.form.get("category", "سایر")
    price = request.form.get("price", "").strip() or "توافقی"
    description = request.form.get("description", "").strip()
    if not title:
        return "عنوان آگهی الزامی است.", 400
    if len(title) > 100 or len(description) > 2000 or len(price) > 30:
        return "اطلاعات آگهی بیش از حد مجاز است.", 400
    if contains_bad_word(title + " " + description):
        return "عنوان یا توضیحات شامل کلمات نامناسب است.", 400
    if category not in CATEGORIES or category == "همه":
        category = "سایر"
    filename = ""
    image = request.files.get("image")
    if image and image.filename:
        if not allowed_file(image.filename):
            return "فرمت تصویر مجاز نیست. فقط JPG/JPEG/PNG/WEBP/GIF.", 400
        safe = secure_filename(image.filename)
        if not safe or "." not in safe:
            return "نام فایل تصویر معتبر نیست.", 400
        ext = safe.rsplit(".", 1)[1].lower()
        filename = uuid.uuid4().hex + "." + ext
        image.save(os.path.join(UPLOAD_FOLDER, filename))
    item = {
        "id": next_listing_id(), "title": title, "category": category, "price": price,
        "description": description, "image": filename, "created_at": now_text(),
        "owner_id": user["id"], "owner_name": user["username"], "active": True,
        "featured_until": "", "last_ladder_at": "", "views": 0, "status": "active",
        "expires_at": iso_after(LISTING_DAYS),
    }
    listings.append(item)
    save_json(FILES["listings"], listings)
    return redirect(url_for("listing_detail", listing_id=item["id"]))


@app.route("/listing/<int:listing_id>")
def listing_detail(listing_id):
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if not active_for_public(item) and not is_admin(user):
        return "این آگهی در حال حاضر غیرفعال است.", 404
    item["views"] = int(item.get("views", 0) or 0) + 1
    save_json(FILES["listings"], listings)
    owner_id = item.get("owner_id") or "legacy"
    owner_name = item.get("owner_name") or "کاربر قدیمی"
    favs = favorites.get(str(user.get("id")), favorites.get(user.get("id"), [])) if user else []
    fav = bool(user and item["id"] in favs)
    body = """<div class='card'>{% if item.image %}<img src='{{url_for("uploaded_file",filename=item.image)}}' style='width:100%;max-height:420px;object-fit:contain;border-radius:14px;background:#eee'>{% endif %}{% if featured_active(item) %}<span class='badge'>⭐ آگهی ویژه</span>{% endif %}<h1>{{item.title}}</h1><div class='price'>{{format_price(item.price)}}</div><div class='meta'>📁 {{item.category}}<br>🕒 {{item.created_at}}<br>👁 بازدید: {{item.views}}<br>📌 وضعیت: {% if item.status=='sold' %}فروخته‌شده{% else %}فعال{% endif %}</div><div class='box'><h3>👤 فروشنده</h3><strong>{{owner_name}}</strong><br><a class='btn light' href='{{url_for("seller_page",seller_id=owner_id)}}'>مشاهده صفحه فروشنده</a>{% if user and user.id|string != owner_id|string %}<form method='post' action='{{url_for("block_user",user_id=owner_id)}}'><input type='hidden' name='next' value='{{request.path}}'><button class='light'>🚫 مسدود کردن فروشنده</button></form>{% endif %}</div><div class='box'><h3>توضیحات</h3><p style='line-height:2;white-space:pre-wrap'>{{item.description or 'توضیحی ثبت نشده است.'}}</p></div><div class='actions'>{% if user %}<a class='btn blue' href='{{url_for("chat",listing_id=item.id)}}'>💬 پیام به فروشنده</a><a class='btn light' href='{{url_for("toggle_favorite",listing_id=item.id)}}'>{% if fav %}💔 حذف از علاقه‌مندی{% else %}❤️ افزودن به علاقه‌مندی{% endif %}</a>{% if user.id==owner_id %}<a class='btn light' href='{{url_for("edit_listing",listing_id=item.id)}}'>✏️ ویرایش</a><a class='btn red' href='{{url_for("delete_listing",listing_id=item.id)}}' onclick='return confirm("حذف شود؟")'>🗑 حذف</a>{% if item.status=='active' %}<a class='btn light' href='{{url_for("mark_sold",listing_id=item.id)}}' onclick='return confirm("آگهی به عنوان فروخته‌شده ثبت شود؟")'>✅ فروخته شد</a>{% else %}<a class='btn green' href='{{url_for("mark_active",listing_id=item.id)}}'>↩️ فعال‌سازی دوباره</a>{% endif %}<a class='btn orange' href='{{url_for("promote",listing_id=item.id)}}'>🚀 درآمدزایی و ارتقا</a>{% endif %}{% else %}<a class='btn blue' href='{{url_for("login",next=request.path)}}'>برای پیام و علاقه‌مندی وارد شو</a>{% endif %}<a class='btn light' href='{{url_for("report_listing",listing_id=item.id)}}'>⚠️ گزارش</a></div></div>"""
    return page(item.get("title", "آگهی"), body, user=user, item=item, owner_id=owner_id, owner_name=owner_name, fav=fav)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("home"))
    err = ""
    nxt = safe_next(request.args.get("next") or request.form.get("next"))
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        if len(name) < 3:
            err = "نام کاربری باید حداقل ۳ کاراکتر باشد."
        elif len(name) > 30:
            err = "نام کاربری بیش از حد طولانی است."
        elif not all(ch.isalnum() or ch in "_-" for ch in name):
            err = "نام کاربری فقط با حروف، عدد، _ یا - باشد."
        elif len(password) < 6:
            err = "رمز عبور باید حداقل ۶ کاراکتر باشد."
        elif password != password2:
            err = "تکرار رمز عبور درست نیست."
        elif get_user_by_username(name):
            err = "این نام کاربری قبلاً ثبت شده است."
        else:
            user = {"id": uuid.uuid4().hex, "username": name, "password": generate_password_hash(password), "created_at": now_text(), "role": "user", "disabled": False}
            users.append(user)
            save_json(FILES["users"], users)
            session.clear()
            session["user_id"] = user["id"]
            return redirect(nxt)
    body = """<div class='card'><h1>ثبت‌نام</h1>{% if err %}<div class='err'>{{err}}</div>{% endif %}<form method='post'><input type='hidden' name='next' value='{{nxt}}'><label>نام کاربری</label><input name='username' minlength='3' maxlength='30' required><label>رمز عبور</label><input type='password' name='password' minlength='6' required><label>تکرار رمز</label><input type='password' name='password2' minlength='6' required><button class='blue'>ثبت‌نام</button></form><p>حساب داری؟ <a style='color:#2563eb' href='{{url_for("login")}}'>ورود</a></p></div>"""
    return page("ثبت‌نام", body, user=None, err=err, nxt=nxt)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("home"))
    err = ""
    nxt = safe_next(request.args.get("next") or request.form.get("next"))
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        if login_rate_limited(ip):
            err = "تعداد تلاش‌های ورود زیاد است. چند دقیقه بعد دوباره تلاش کن."
            return page("ورود", """<div class='card'><h1>ورود</h1><div class='err'>{{err}}</div><p>لطفاً چند دقیقه صبر کن و دوباره تلاش کن.</p></div>""", user=None, err=err)
        name = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_user_by_username(name)
        valid = False
        if user:
            try:
                valid = check_password_hash(user.get("password", ""), password)
            except Exception:
                valid = False
        if valid and user.get("disabled", False):
            err = "این حساب توسط مدیریت غیرفعال شده است."
            record_login_failure(ip)
        elif valid:
            clear_login_failures(ip)
            session.clear()
            session["user_id"] = user["id"]
            return redirect(nxt)
        else:
            record_login_failure(ip)
            err = "نام کاربری یا رمز عبور اشتباه است."
    body = """<div class='card'><h1>ورود</h1>{% if err %}<div class='err'>{{err}}</div>{% endif %}<form method='post'><input type='hidden' name='next' value='{{nxt}}'><label>نام کاربری</label><input name='username' required><label>رمز عبور</label><input type='password' name='password' required><button class='blue'>ورود</button></form><p>حساب نداری؟ <a style='color:#2563eb' href='{{url_for("register")}}'>ثبت‌نام</a></p></div>"""
    return page("ورود", body, user=None, err=err, nxt=nxt)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/profile")
@login_required
def profile():
    user = current_user()
    mine = [x for x in listings if str(x.get("owner_id")) == str(user["id"])]
    mine.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    fav_ids = favorites.get(str(user["id"]), favorites.get(user["id"], []))
    favorite_count = len(fav_ids) if isinstance(fav_ids, list) else 0
    views = sum(int(x.get("views", 0) or 0) for x in mine)
    featured_count = sum(1 for x in mine if featured_active(x))
    body = """<div class='card'><div class='profile'><div class='avatar'>{{user.username[:1]|upper}}</div><div><h1>{{user.username}}</h1><div class='small'>عضویت: {{user.created_at}}</div></div></div><div class='statgrid' style='margin-top:15px'><div class='stat'><b>{{mine|length}}</b>آگهی</div><div class='stat'><b>{{favorite_count}}</b>علاقه‌مندی</div><div class='stat'><b>{{views}}</b>بازدید</div><div class='stat'><b>{{featured_count}}</b>ویژه فعال</div></div><div class='actions'><a class='btn light' href='{{url_for("settings")}}'>⚙️ تنظیمات</a><a class='btn red' href='{{url_for("logout")}}'>خروج</a><form method='post' action='{{url_for("delete_account")}}' onsubmit='return confirm("حذف حساب و آگهی‌های آن قطعی است؟")'><button class='red'>🗑 حذف حساب</button></form></div></div><div class='card'><h2>آگهی‌های من</h2></div><div class='grid'>{% for item in mine %}""" + CARD + """{% else %}<div class='card'>هنوز آگهی‌ای ثبت نکرده‌ای.</div>{% endfor %}</div>"""
    return page("پروفایل", body, user=user, mine=mine, favorite_count=favorite_count, views=views, featured_count=featured_count, user_favs=fav_ids)


@app.route("/seller/")
@app.route("/seller/<seller_id>")
def seller_page(seller_id="legacy"):
    seller = get_user_by_id(seller_id)
    name = seller.get("username", "فروشنده") if seller else ("کاربر قدیمی" if seller_id == "legacy" else "فروشنده")
    items = [x for x in listings if str(x.get("owner_id") or "legacy") == str(seller_id)]
    if not is_admin(current_user()):
        items = [x for x in items if active_for_public(x)]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    user = current_user()
    favs = favorites.get(str(user.get("id")), favorites.get(user.get("id"), [])) if user else []
    rating = rating_for_seller(seller_id)
    body = """<div class='card'><div class='profile'><div class='avatar'>{{name[:1]|upper}}</div><div><h1>{{name}}</h1><div class='small'>🛍 فروشنده کالا مود</div></div></div><div class='box'><h3>⭐ امتیاز فروشنده</h3><div style='font-size:24px'>{{rating.average}} / 5</div><div class='small'>بر اساس {{rating.count}} امتیاز</div></div><hr><h3>آگهی‌های این فروشنده</h3><div class='small'>تعداد: {{items|length}}</div></div><div class='grid'>{% for item in items %}""" + CARD + """{% else %}<div class='card'>این فروشنده هنوز آگهی‌ای ثبت نکرده است.</div>{% endfor %}</div>"""
    if user and str(user.get("id")) != str(seller_id):
        body += """<div class='card'><h2>⭐ ثبت امتیاز</h2><p class='small'>فقط برای آگهی‌های فروخته‌شده‌ای که درباره‌شان گفتگو داشته‌اید می‌توانید امتیاز ثبت کنید.</p>{% for item in all_seller_items if item.status=='sold' %}{% if item.id in rated_ids %}<div class='box'>{{item.title}} — امتیاز شما ثبت شده است.</div>{% elif item.id in chatted_ids %}<form method='post' action='{{url_for("rate_seller",listing_id=item.id)}}' class='box'><b>{{item.title}}</b><select name='score' required><option value=''>انتخاب امتیاز</option><option value='5'>★★★★★ عالی</option><option value='4'>★★★★ خوب</option><option value='3'>★★★ متوسط</option><option value='2'>★★ ضعیف</option><option value='1'>★ خیلی ضعیف</option></select><input name='comment' maxlength='300' placeholder='نظر کوتاه (اختیاری)'><button class='blue'>ثبت امتیاز</button></form>{% endif %}{% endfor %}</div>"""
    rated_ids = {int(r.get("listing_id")) for r in ratings if isinstance(r, dict) and str(r.get("user_id")) == str(user.get("id")) and str(r.get("listing_id", "")).isdigit()} if user else set()
    chatted_ids = set()
    if user:
        for x in items:
            chat_rows = messages.get(str(x.get("id")), [])
            if any(isinstance(m, dict) and str(m.get("user_id")) == str(user.get("id")) for m in chat_rows):
                chatted_ids.add(int(x.get("id")))
    return page("فروشنده - " + name, body, user=user, name=name, items=items, all_seller_items=items, user_favs=favs, rating=rating, rated_ids=rated_ids, chatted_ids=chatted_ids)


@app.route("/rate/<int:listing_id>", methods=["POST"])
@login_required
def rate_seller(listing_id):
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if item.get("status") != "sold":
        return "امتیازدهی فقط بعد از فروخته‌شدن آگهی فعال می‌شود.", 400
    seller_id = str(item.get("owner_id"))
    if seller_id == str(user.get("id")):
        return "نمی‌توانی به خودت امتیاز بدهی.", 400
    chat_rows = messages.get(str(listing_id), [])
    if not any(isinstance(m, dict) and str(m.get("user_id")) == str(user.get("id")) for m in chat_rows):
        return "برای ثبت امتیاز ابتدا باید درباره این آگهی گفتگو داشته باشید.", 403
    if user_rated_listing(user.get("id"), listing_id):
        return redirect(url_for("seller_page", seller_id=seller_id))
    try:
        score = int(request.form.get("score", "0"))
    except Exception:
        score = 0
    if score < 1 or score > 5:
        return "امتیاز باید بین ۱ تا ۵ باشد.", 400
    comment = request.form.get("comment", "").strip()[:300]
    if contains_bad_word(comment):
        return "نظر شامل کلمات نامناسب است.", 400
    ratings.append({"id": uuid.uuid4().hex, "listing_id": listing_id, "seller_id": seller_id, "user_id": user.get("id"), "username": user.get("username"), "score": score, "comment": comment, "created_at": now_text()})
    save_json(FILES["ratings"], ratings)
    create_notification(seller_id, "امتیاز جدید", f"برای آگهی «{item.get('title','')}» امتیاز جدید ثبت شد.", url_for("seller_page", seller_id=seller_id))
    return redirect(url_for("seller_page", seller_id=seller_id))

@app.route("/block/<user_id>", methods=["POST"])
@login_required
def block_user(user_id):
    user = current_user()
    target = get_user_by_id(user_id)
    if not target:
        return "کاربر پیدا نشد.", 404
    if str(user.get("id")) == str(target.get("id")):
        return "نمی‌توانی خودت را مسدود کنی.", 400
    add_block(user.get("id"), target.get("id"))
    create_notification(user.get("id"), "کاربر مسدود شد", f"کاربر «{target.get('username','')}» مسدود شد.", url_for("profile"))
    return redirect(safe_next(request.form.get("next")))


@app.route("/unblock/<user_id>", methods=["POST"])
@login_required
def unblock_user(user_id):
    user = current_user()
    remove_block(user.get("id"), user_id)
    return redirect(safe_next(request.form.get("next")))


@app.route("/blocked")
@login_required
def blocked_page():
    user = current_user()
    ids = [str(x) for x in blocks.get(str(user.get("id")), [])]
    blocked_users = [u for u in users if str(u.get("id")) in ids]
    body = """<div class='card'><h1>🚫 کاربران مسدودشده</h1><p class='small'>کاربران مسدودشده نمی‌توانند در گفتگو با شما ارتباط برقرار کنند.</p></div><div class='card'>{% for u in blocked_users %}<div class='box'><b>{{u.username}}</b><form method='post' action='{{url_for("unblock_user",user_id=u.id)}}'><input type='hidden' name='next' value='{{request.path}}'><button class='green'>رفع مسدودی</button></form></div>{% else %}<p>هنوز کاربری را مسدود نکرده‌ای.</p>{% endfor %}</div>"""
    return page("کاربران مسدودشده", body, user=user, blocked_users=blocked_users)


@app.route("/favorites")
@login_required
def favorites_page():
    user = current_user()
    ids = favorites.get(str(user["id"]), favorites.get(user["id"], []))
    if not isinstance(ids, list):
        ids = []
    items = [x for x in listings if x.get("id") in ids and active_for_public(x)]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    body = """<div class='card'><h1>❤️ علاقه‌مندی‌ها</h1><div class='small'>تعداد: {{items|length}}</div></div><div class='grid'>{% for item in items %}""" + CARD + """{% else %}<div class='card'>هنوز آگهی‌ای اضافه نکرده‌ای.</div>{% endfor %}</div>"""
    return page("علاقه‌مندی‌ها", body, user=user, items=items, user_favs=ids)


@app.route("/favorite/<int:listing_id>")
@login_required
def toggle_favorite(listing_id):
    item = find_listing(listing_id)
    if not item:
        return "آگهی پیدا نشد.", 404
    user = current_user()
    uid = str(user["id"])
    ids = favorites.setdefault(uid, [])
    normalized = []
    for value in ids:
        try:
            n = int(value)
            if n not in normalized:
                normalized.append(n)
        except Exception:
            pass
    if listing_id in normalized:
        normalized.remove(listing_id)
    else:
        normalized.append(listing_id)
    favorites[uid] = normalized
    save_json(FILES["favorites"], favorites)
    return redirect(request.referrer or url_for("home"))


@app.route("/chat/<int:listing_id>", methods=["GET", "POST"])
@login_required
def chat(listing_id):
    item = find_listing(listing_id)
    if not item:
        return "آگهی پیدا نشد.", 404
    user = current_user()
    if not active_for_public(item) and not is_admin(user):
        return "این آگهی غیرفعال است.", 404
    owner_id = str(item.get("owner_id"))
    if str(user["id"]) != owner_id and is_blocked(user.get("id"), owner_id):
        body = """<div class='card center'><h1>🚫 گفتگو در دسترس نیست</h1><p>ارتباط این گفتگو به دلیل مسدود بودن یکی از طرفین فعال نیست.</p><a class='btn blue' href='{{url_for("listing_detail",listing_id=item.id)}}'>بازگشت به آگهی</a></div>"""
        return page("گفتگو مسدود است", body, user=user, item=item)
    if str(user["id"]) == owner_id:
        role = "فروشنده"
    else:
        role = "خریدار"
    key = str(listing_id)
    messages.setdefault(key, [])
    if not isinstance(messages[key], list):
        messages[key] = []
    if request.method == "POST":
        text = request.form.get("message", "").strip()
        if not text:
            return redirect(url_for("chat", listing_id=listing_id))
        if len(text) > 1000:
            return "پیام بیش از حد طولانی است.", 400
        if contains_bad_word(text):
            body = """<div class='card'><div class='err'>این پیام به دلیل استفاده از کلمات نامناسب ارسال نشد.</div><a class='btn' href='{{url_for("chat",listing_id=item.id)}}'>بازگشت</a></div>"""
            return page("پیام ارسال نشد", body, user=user, item=item)
        messages[key].append({"id": uuid.uuid4().hex, "user_id": user["id"], "username": user["username"], "role": role, "text": text, "time": now_text()})
        save_json(FILES["messages"], messages)
        if role == "خریدار":
            create_notification(owner_id, "پیام جدید", f"برای آگهی «{item.get('title','')}» پیام جدید داری.", url_for("chat", listing_id=listing_id))
        else:
            buyer_ids = set(str(m.get("user_id")) for m in messages[key] if isinstance(m, dict) and str(m.get("user_id")) != owner_id and m.get("user_id") != "legacy")
            for buyer_id in buyer_ids:
                create_notification(buyer_id, "پاسخ فروشنده", f"فروشنده آگهی «{item.get('title','')}» پاسخ داده است.", url_for("chat", listing_id=listing_id))
        return redirect(url_for("chat", listing_id=listing_id))
    body = """<div class='card'><h2>💬 گفتگو درباره {{item.title}}</h2><div class='box'><strong>فروشنده: {{item.owner_name}}</strong><br><span class='small'>شما به عنوان «{{role}}» پیام می‌فرستید.</span></div></div><div class='card'>{% for m in chat_messages %}<div class='msg {% if m.get("user_id")|string == user.id|string %}me{% else %}other{% endif %}'><div class='small'>{{m.get("username","کاربر")}} - {{m.get("role","کاربر")}}</div><div style='white-space:pre-wrap'>{{m.get("text","")}}</div><div class='small'>{{m.get("time","")}}</div>{% if m.get("id") %}<form method='post' action='{{url_for("report_message",listing_id=item.id,message_id=m.id)}}'><button class='light' style='padding:6px 9px;font-size:11px'>🚨 گزارش پیام</button></form>{% endif %}</div>{% else %}<div class='center small'>هنوز پیامی نیست.</div>{% endfor %}</div><div class='card'><form method='post'><textarea name='message' maxlength='1000' required placeholder='پیامت را بنویس...'></textarea><button class='blue'>ارسال پیام</button></form></div>"""
    return page("گفتگو", body, user=user, item=item, role=role, chat_messages=messages[key])


@app.route("/edit/<int:listing_id>", methods=["GET", "POST"])
@login_required
def edit_listing(listing_id):
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if str(item.get("owner_id")) != str(user["id"]):
        return "شما مالک این آگهی نیستید.", 403
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "سایر")
        price = request.form.get("price", "").strip() or "توافقی"
        description = request.form.get("description", "").strip()
        if not title:
            return "عنوان الزامی است.", 400
        if len(title) > 100 or len(price) > 30 or len(description) > 2000:
            return "اطلاعات بیش از حد مجاز است.", 400
        if contains_bad_word(title + " " + description):
            return "عنوان یا توضیحات شامل کلمات نامناسب است.", 400
        item["title"] = title
        item["category"] = category if category in CATEGORIES and category != "همه" else "سایر"
        item["price"] = price
        item["description"] = description
        image = request.files.get("image")
        if image and image.filename:
            if not allowed_file(image.filename):
                return "فرمت تصویر مجاز نیست.", 400
            old = item.get("image")
            if old:
                old_path = os.path.join(UPLOAD_FOLDER, os.path.basename(old))
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass
            safe = secure_filename(image.filename)
            ext = safe.rsplit(".", 1)[1].lower()
            filename = uuid.uuid4().hex + "." + ext
            image.save(os.path.join(UPLOAD_FOLDER, filename))
            item["image"] = filename
        save_json(FILES["listings"], listings)
        return redirect(url_for("listing_detail", listing_id=listing_id))
    body = """<div class='card'><h1>ویرایش آگهی</h1><form method='post' enctype='multipart/form-data'><label>عنوان</label><input name='title' value='{{item.title}}' maxlength='100' required><label>دسته‌بندی</label><select name='category'>{% for c in categories if c!='همه' %}<option {% if c==item.category %}selected{% endif %}>{{c}}</option>{% endfor %}</select><label>قیمت</label><input name='price' value='{{format_price(item.price)}}' maxlength='30'><label>توضیحات</label><textarea name='description' maxlength='2000'>{{item.description}}</textarea><label>تصویر جدید</label><input type='file' name='image' accept='.jpg,.jpeg,.png,.webp,.gif'><button class='green'>ذخیره تغییرات</button></form></div>"""
    return page("ویرایش", body, user=user, item=item, categories=CATEGORIES)


@app.route("/delete/<int:listing_id>")
@login_required
def delete_listing(listing_id):
    global listings
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if str(item.get("owner_id")) != str(user["id"]):
        return "دسترسی غیرمجاز.", 403
    image = item.get("image")
    if image:
        path = os.path.join(UPLOAD_FOLDER, os.path.basename(image))
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    listings = [x for x in listings if str(x.get("id")) != str(listing_id)]
    messages.pop(str(listing_id), None)
    for uid in list(favorites.keys()):
        favorites[uid] = [i for i in favorites[uid] if str(i) != str(listing_id)] if isinstance(favorites[uid], list) else []
    save_json(FILES["listings"], listings)
    save_json(FILES["messages"], messages)
    save_json(FILES["favorites"], favorites)
    return redirect(url_for("profile"))


@app.route("/sold/<int:listing_id>")
@login_required
def mark_sold(listing_id):
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if str(item.get("owner_id")) != str(user["id"]):
        return "دسترسی غیرمجاز.", 403
    item["status"] = "sold"
    item["active"] = False
    item["featured_until"] = ""
    save_json(FILES["listings"], listings)
    create_notification(user["id"], "آگهی فروخته شد", f"آگهی «{item.get('title','')}» به عنوان فروخته‌شده ثبت شد.", url_for("profile"))
    return redirect(url_for("profile"))


@app.route("/activate/<int:listing_id>")
@login_required
def mark_active(listing_id):
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if str(item.get("owner_id")) != str(user["id"]):
        return "دسترسی غیرمجاز.", 403
    item["status"] = "active"
    item["active"] = True
    item["created_at"] = now_text()
    item["expires_at"] = iso_after(LISTING_DAYS)
    save_json(FILES["listings"], listings)
    create_notification(user["id"], "آگهی دوباره فعال شد", f"آگهی «{item.get('title','')}» دوباره فعال شد.", url_for("listing_detail", listing_id=listing_id))
    return redirect(url_for("listing_detail", listing_id=listing_id))


@app.route("/renew/<int:listing_id>")
@login_required
def renew_listing(listing_id):
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if str(item.get("owner_id")) != str(user["id"]):
        return "دسترسی غیرمجاز.", 403
    item["status"] = "active"
    item["active"] = True
    item["expires_at"] = iso_after(LISTING_DAYS)
    item["created_at"] = now_text()
    save_json(FILES["listings"], listings)
    create_notification(user["id"], "آگهی تمدید شد", f"آگهی «{item.get('title','')}» برای {LISTING_DAYS} روز دیگر فعال شد.", url_for("listing_detail", listing_id=listing_id))
    return redirect(url_for("listing_detail", listing_id=listing_id))


@app.route("/share/<int:listing_id>")
def share_listing(listing_id):
    item = find_listing(listing_id)
    if not item or (not active_for_public(item) and not is_admin(current_user())):
        return "آگهی پیدا نشد.", 404
    share_url = request.host_url.rstrip("/") + url_for("listing_detail", listing_id=listing_id)
    body = """<div class='card center'><h1>🔗 اشتراک‌گذاری آگهی</h1><p>{{item.title}}</p><div class='box' style='word-break:break-all'>{{share_url}}</div><div class='actions' style='justify-content:center'><button class='blue' onclick='navigator.clipboard && navigator.clipboard.writeText({{share_url|tojson}}).then(function(){alert("لینک کپی شد")})'>📋 کپی لینک</button><a class='btn light' href='{{url_for("listing_detail",listing_id=item.id)}}'>بازگشت به آگهی</a></div></div>"""
    return page("اشتراک‌گذاری", body, user=current_user(), item=item, share_url=share_url)


@app.route("/promote/<int:listing_id>")
@login_required
def promote(listing_id):
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if str(item.get("owner_id")) != str(user["id"]):
        return "فقط صاحب آگهی می‌تواند آن را ارتقا دهد.", 403
    body = """<div class='card'><h1>🚀 ارتقای آگهی</h1><div class='box'><h3>⭐ آگهی ویژه</h3><p>آگهی ویژه برای ۳ روز در بخش بالای صفحه نمایش داده می‌شود.</p><a class='btn orange' href='{{url_for("activate_featured",listing_id=item.id)}}'>فعال‌سازی آگهی ویژه</a></div><div class='box'><h3>⬆️ نردبان</h3><p>با نردبان، آگهی دوباره در ابتدای لیست جدیدترین‌ها قرار می‌گیرد.</p><a class='btn purple' href='{{url_for("ladder_listing",listing_id=item.id)}}'>اجرای نردبان</a></div><div class='box'><span class='small'>در این نسخه محلی، عملیات رایگان است تا منطق سیستم را کامل تست کنیم. در مرحله پرداخت، قیمت و درگاه اضافه می‌شود.</span></div><a class='btn light' href='{{url_for("listing_detail",listing_id=item.id)}}'>بازگشت</a></div>"""
    return page("ارتقای آگهی", body, user=user, item=item)


@app.route("/promote/<int:listing_id>/featured")
@login_required
def activate_featured(listing_id):
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if str(item.get("owner_id")) != str(user["id"]):
        return "دسترسی غیرمجاز.", 403
    current_until = parse_iso(item.get("featured_until", ""))
    start = current_until if current_until and current_until > datetime.now() else datetime.now()
    item["featured_until"] = (start + timedelta(days=PROMOTE_DAYS)).isoformat(timespec="seconds")
    save_json(FILES["listings"], listings)
    create_notification(user["id"], "آگهی ویژه فعال شد", f"آگهی «{item.get('title','')}» تا {item['featured_until']} ویژه است.", url_for("listing_detail", listing_id=listing_id))
    return redirect(url_for("listing_detail", listing_id=listing_id))


@app.route("/promote/<int:listing_id>/ladder")
@login_required
def ladder_listing(listing_id):
    item = find_listing(listing_id)
    user = current_user()
    if not item:
        return "آگهی پیدا نشد.", 404
    if str(item.get("owner_id")) != str(user["id"]):
        return "دسترسی غیرمجاز.", 403
    last = parse_iso(item.get("last_ladder_at", ""))
    if last and datetime.now() - last < timedelta(hours=12):
        return "نردبان این آگهی در ۱۲ ساعت گذشته اجرا شده است. کمی بعد دوباره امتحان کن.", 400
    item["last_ladder_at"] = iso_now()
    item["created_at"] = now_text()
    save_json(FILES["listings"], listings)
    create_notification(user["id"], "نردبان انجام شد", f"آگهی «{item.get('title','')}» به ابتدای آگهی‌های جدید منتقل شد.", url_for("listing_detail", listing_id=listing_id))
    return redirect(url_for("listing_detail", listing_id=listing_id))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, os.path.basename(filename))


@app.route("/messages")
@login_required
def inbox():
    user = current_user()
    uid = str(user["id"])
    rows = []
    for lid, chat_items in messages.items():
        if not isinstance(chat_items, list) or not chat_items:
            continue
        item = find_listing(lid)
        if not item:
            continue
        related = [m for m in chat_items if isinstance(m, dict) and (str(m.get("user_id")) == uid or str(item.get("owner_id")) == uid)]
        if not related:
            continue
        last = related[-1]
        rows.append({"listing": item, "last": last, "count": len(related)})
    rows.sort(key=lambda x: str(x["last"].get("time", "")), reverse=True)
    body = """<div class='card'><h1>💬 پیام‌های من</h1><div class='small'>گفتگوهای مربوط به آگهی‌ها</div></div>{% for row in rows %}<div class='card'><h3>{{row.listing.title}}</h3><div class='small'>آخرین پیام از {{row.last.get('username','کاربر')}} · {{row.last.get('time','')}} · {{row.count}} پیام</div><p style='white-space:pre-wrap'>{{row.last.get('text','')}}</p><a class='btn blue' href='{{url_for("chat",listing_id=row.listing.id)}}'>باز کردن گفتگو</a></div>{% else %}<div class='card center'>هنوز گفتگویی نداری.</div>{% endfor %}"""
    return page("پیام‌های من", body, user=user, rows=rows)


@app.route("/notifications")
@login_required
def notifications_page():
    user = current_user()
    items = [n for n in notifications if str(n.get("user_id")) == str(user["id"])]
    items.sort(key=lambda n: n.get("created_at", ""), reverse=True)
    changed = False
    for n in notifications:
        if str(n.get("user_id")) == str(user["id"]) and not n.get("read", False):
            n["read"] = True
            changed = True
    if changed:
        save_json(FILES["notifications"], notifications)
    body = """<div class='card'><h1>🔔 اعلان‌ها</h1><form method='post' action='{{url_for("notifications_read_all")}}'><button class='light'>✓ همه خوانده شد</button></form>{% for n in items %}<div class='box'><h3>{{n.title}}</h3><p>{{n.text}}</p><div class='small'>{{n.created_at}}</div>{% if n.target %}<a class='btn light' href='{{n.target}}'>مشاهده</a>{% endif %}</div>{% else %}<p>اعلانی ندارید.</p>{% endfor %}</div>"""
    return page("اعلان‌ها", body, user=user, items=items)


@app.route("/report/listing/<int:listing_id>", methods=["GET", "POST"])
@login_required
def report_listing(listing_id):
    item = find_listing(listing_id)
    if not item:
        return "آگهی پیدا نشد.", 404
    user = current_user()
    if request.method == "POST":
        reason = request.form.get("reason", "سایر").strip()
        details = request.form.get("details", "").strip()[:1000]
        reports.append({"id": uuid.uuid4().hex, "type": "listing", "listing_id": listing_id, "reporter_id": user.get("id"), "username": user.get("username"), "reason": reason, "details": details, "status": "new", "created_at": now_text()})
        save_json(FILES["reports"], reports)
        admin = admin_user()
        if admin:
            create_notification(admin.get("id"), "گزارش جدید", f"آگهی شماره {listing_id} گزارش شده است.", url_for("admin_panel"))
        return page("گزارش ثبت شد", "<div class='card center'><h2>گزارش ثبت شد ✅</h2><p>گزارش برای مدیریت ارسال شد.</p></div>", user=user)
    body = """<div class='card'><h1>🚨 گزارش آگهی</h1><form method='post'><label>دلیل</label><select name='reason' required><option>کلاهبرداری یا مورد مشکوک</option><option>محتوای نامناسب</option><option>اطلاعات نادرست</option><option>آگهی تکراری</option><option>سایر</option></select><label>توضیحات</label><textarea name='details' maxlength='1000' placeholder='توضیحات بیشتر...'></textarea><button class='red'>ثبت گزارش</button></form></div>"""
    return page("گزارش آگهی", body, user=user)


@app.route("/report/message/<int:listing_id>/<message_id>", methods=["POST"])
@login_required
def report_message(listing_id, message_id):
    item = find_listing(listing_id)
    key = str(listing_id)
    msg = next((m for m in messages.get(key, []) if isinstance(m, dict) and str(m.get("id")) == str(message_id)), None)
    if not item or not msg:
        return "پیام پیدا نشد.", 404
    user = current_user()
    reports.append({"id": uuid.uuid4().hex, "type": "message", "listing_id": listing_id, "message_id": message_id, "reporter_id": user.get("id"), "username": user.get("username"), "reason": "گزارش پیام", "details": "", "message_text": str(msg.get("text", ""))[:500], "status": "new", "created_at": now_text()})
    save_json(FILES["reports"], reports)
    admin = admin_user()
    if admin:
        create_notification(admin.get("id"), "گزارش پیام جدید", f"یک پیام در آگهی {listing_id} گزارش شده است.", url_for("admin_panel"))
    return redirect(url_for("chat", listing_id=listing_id))


@app.route("/support", methods=["GET", "POST"])
def support():
    user = current_user()
    ok = ""
    text = ""
    if request.method == "POST":
        text = request.form.get("message", "").strip()[:2000]
        if text:
            reports.append({"id": uuid.uuid4().hex, "type": "support", "user_id": user["id"] if user else None, "username": user["username"] if user else "مهمان", "message": text, "status": "new", "created_at": now_text()})
            save_json(FILES["reports"], reports)
            admin = admin_user()
            if admin:
                create_notification(admin.get("id"), "پیام پشتیبانی جدید", "یک پیام جدید در پشتیبانی ثبت شده است.", url_for("admin_panel"))
            ok = "پیام شما با موفقیت ثبت شد."
            text = ""
    body = """<div class='card'><h1>پشتیبانی و گزارش</h1>{% if ok %}<div class='ok'>{{ok}}</div>{% endif %}<form method='post'><textarea name='message' maxlength='2000' required placeholder='مشکل یا گزارش...'>{{text}}</textarea><button class='blue'>ارسال</button></form></div>"""
    return page("پشتیبانی", body, user=user, ok=ok, text=text)


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    user = current_user()
    changed = False
    for n in notifications:
        if str(n.get("user_id")) == str(user["id"]) and not n.get("read", False):
            n["read"] = True
            changed = True
    if changed:
        save_json(FILES["notifications"], notifications)
    return redirect(url_for("notifications_page"))


@app.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    user = current_user()
    err = ""
    ok = ""
    if request.method == "POST":
        old = request.form.get("old_password", "")
        new = request.form.get("new_password", "")
        new2 = request.form.get("new_password2", "")
        valid_old = False
        try:
            valid_old = check_password_hash(user.get("password", ""), old)
        except Exception:
            valid_old = False
        if not valid_old:
            err = "رمز عبور فعلی درست نیست."
        elif len(new) < 6:
            err = "رمز جدید باید حداقل ۶ کاراکتر باشد."
        elif new != new2:
            err = "تکرار رمز جدید درست نیست."
        else:
            user["password"] = generate_password_hash(new)
            save_json(FILES["users"], users)
            ok = "رمز عبور با موفقیت تغییر کرد."
    body = """<div class='card'><h1>🔐 تغییر رمز عبور</h1>{% if err %}<div class='err'>{{err}}</div>{% endif %}{% if ok %}<div class='ok'>{{ok}}</div>{% endif %}<form method='post'><label>رمز فعلی</label><input type='password' name='old_password' required><label>رمز جدید</label><input type='password' name='new_password' minlength='6' required><label>تکرار رمز جدید</label><input type='password' name='new_password2' minlength='6' required><button class='blue'>تغییر رمز</button></form><a class='btn light' href='{{url_for("settings")}}'>بازگشت</a></div>"""
    return page("تغییر رمز", body, user=user, err=err, ok=ok)


@app.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    user = current_user()
    if is_admin(user):
        return "حساب مدیر اصلی قابل حذف نیست.", 400
    uid = str(user.get("id"))
    owned = [x for x in listings if str(x.get("owner_id")) == uid]
    for item in owned:
        image = item.get("image")
        if image:
            path = os.path.join(UPLOAD_FOLDER, os.path.basename(image))
            if os.path.exists(path):
                try: os.remove(path)
                except Exception: pass
        messages.pop(str(item.get("id")), None)
    listings[:] = [x for x in listings if str(x.get("owner_id")) != uid]
    users[:] = [u for u in users if str(u.get("id")) != uid]
    favorites.pop(uid, None)
    notifications[:] = [n for n in notifications if str(n.get("user_id")) != uid]
    reports[:] = [r for r in reports if str(r.get("user_id")) != uid]
    save_json(FILES["listings"], listings); save_json(FILES["users"], users); save_json(FILES["messages"], messages)
    save_json(FILES["favorites"], favorites); save_json(FILES["notifications"], notifications); save_json(FILES["reports"], reports)
    session.clear()
    return redirect(url_for("home"))


@app.route("/settings")
@login_required
def settings():
    user = current_user()
    body = """<div class='card'><h1>⚙️ تنظیمات</h1><div class='box'><strong>نام کاربری</strong><br>{{user.username}}</div><div class='box'><strong>شناسه کاربر</strong><br><span class='small'>{{user.id}}</span></div><div class='box'><strong>وضعیت حساب</strong><br>{% if user.disabled %}مسدود{% else %}فعال{% endif %}</div><div class='actions'><a class='btn light' href='{{url_for("profile")}}'>👤 پروفایل</a><a class='btn light' href='{{url_for("inbox")}}'>💬 پیام‌ها</a><a class='btn purple' href='{{url_for("change_password")}}'>🔐 تغییر رمز</a><a class='btn light' href='{{url_for("blocked_page")}}'>🚫 مسدودشده‌ها</a><a class='btn red' href='{{url_for("logout")}}'>خروج</a><form method='post' action='{{url_for("delete_account")}}' onsubmit='return confirm("حذف حساب و آگهی‌های آن قطعی است؟")'><button class='red'>🗑 حذف حساب</button></form></div></div>"""
    return page("تنظیمات", body, user=user)


@app.route("/admin")
@admin_required
def admin_panel():
    query = request.args.get("q", "").strip().lower()
    status = request.args.get("status", "").strip()
    listing_filter = request.args.get("listing_status", "").strip()
    ls = [x for x in listings if not query or query in str(x.get("title", "")).lower() or query in str(x.get("id", "")).lower()]
    us = [u for u in users if not query or query in str(u.get("username", "")).lower()]
    rs = [r for r in reports if not status or r.get("status") == status]
    if listing_filter == "active":
        ls = [x for x in ls if x.get("active", True)]
    elif listing_filter == "inactive":
        ls = [x for x in ls if not x.get("active", True)]
    body = """<div class='statgrid'><div class='stat'><b>{{lc}}</b>آگهی</div><div class='stat'><b>{{uc}}</b>کاربر</div><div class='stat'><b>{{rc}}</b>گزارش</div><div class='stat'><b>{{fc}}</b>ویژه فعال</div><div class='stat'><b>{{views}}</b>بازدید</div><div class='stat'><b>{{sold}}</b>فروخته‌شده</div></div><div class='card'><h1>🛠 پنل مدیریت</h1><div class='actions'><a class='btn purple' href='{{url_for("admin_backup")}}'>💾 پشتیبان‌گیری</a><a class='btn blue' href='{{url_for("admin_analytics")}}'>📊 آمار کامل</a></div><form method='get'><input name='q' value='{{q}}' placeholder='جستجوی آگهی یا کاربر'><select name='status'><option value=''>همه گزارش‌ها</option><option value='new' {% if status=='new' %}selected{% endif %}>جدید</option><option value='resolved' {% if status=='resolved' %}selected{% endif %}>رسیدگی‌شده</option></select><select name='listing_status'><option value=''>همه آگهی‌ها</option><option value='active' {% if listing_filter=='active' %}selected{% endif %}>فعال</option><option value='inactive' {% if listing_filter=='inactive' %}selected{% endif %}>غیرفعال</option></select><button>فیلتر</button></form></div><div class='card'><h2>📦 آگهی‌ها</h2>{% for x in ls[:100] %}<div class='box'><b>#{{x.id}} — {{x.title}}</b><div class='small'>{{x.price}} · {{x.category}} · {% if x.active %}فعال{% else %}غیرفعال{% endif %} · بازدید {{x.views}}</div><div class='actions'><form method='post' action='{{url_for("admin_toggle_listing",listing_id=x.id)}}'><button class='light'>{% if x.active %}غیرفعال کردن{% else %}فعال کردن{% endif %}</button></form><a class='btn' href='{{url_for("listing_detail",listing_id=x.id)}}'>مشاهده</a><form method='post' action='{{url_for("admin_delete_listing",listing_id=x.id)}}' onsubmit='return confirm("حذف شود؟")'><button class='red'>حذف</button></form></div></div>{% else %}<p>آگهی‌ای نیست.</p>{% endfor %}</div><div class='card'><h2>👥 کاربران</h2>{% for u in us[:100] %}<div class='box'><b>{{u.username}}</b> · {{u.role}} · {% if u.disabled %}مسدود{% else %}فعال{% endif %}{% if u.role!='admin' %}<form method='post' action='{{url_for("admin_toggle_user",user_id=u.id)}}'><button class='light'>{% if u.disabled %}رفع مسدودی{% else %}مسدود کردن{% endif %}</button></form>{% endif %}</div>{% endfor %}</div><div class='card'><h2>🚨 گزارش‌ها</h2>{% for r in rs[:100] %}<div class='box'><b>{{r.get('type','گزارش')}}</b> · {{r.get('reason','')}} · {{r.get('status','new')}}</div><p>{{r.get('details',r.get('message',''))}}</p>{% if r.get('message_text') %}<p>پیام: {{r.message_text}}</p>{% endif %}<div class='small'>{{r.get('created_at','')}}</div>{% if r.get('status')!='resolved' %}<form method='post' action='{{url_for("admin_resolve_report",report_id=r.id)}}'><button>رسیدگی شد</button></form>{% endif %}{% else %}<p>گزارشی نیست.</p>{% endfor %}</div>"""
    return page("پنل مدیریت", body, user=current_user(), ls=ls, us=us, rs=rs, lc=len(listings), uc=len(users), rc=len(reports), fc=sum(1 for x in listings if featured_active(x)), views=sum(int(x.get('views',0) or 0) for x in listings), sold=sum(1 for x in listings if x.get('status')=='sold'), q=query, status=status, listing_filter=listing_filter)


@app.route("/admin/listing/toggle/<int:listing_id>", methods=["POST"])
@admin_required
def admin_toggle_listing(listing_id):
    item = find_listing(listing_id)
    if not item:
        return "آگهی پیدا نشد.", 404
    item["active"] = not item.get("active", True)
    save_json(FILES["listings"], listings)
    create_notification(item.get("owner_id"), "وضعیت آگهی", f"آگهی «{item.get('title','')}» {'فعال' if item['active'] else 'غیرفعال'} شد.", url_for("listing_detail", listing_id=listing_id))
    return redirect(url_for("admin_panel"))


@app.route("/admin/listing/delete/<int:listing_id>", methods=["POST"])
@admin_required
def admin_delete_listing(listing_id):
    global listings
    item = find_listing(listing_id)
    if not item:
        return "آگهی پیدا نشد.", 404
    image = item.get("image")
    if image:
        path = os.path.join(UPLOAD_FOLDER, os.path.basename(image))
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    listings = [x for x in listings if str(x.get("id")) != str(listing_id)]
    messages.pop(str(listing_id), None)
    for uid in list(favorites.keys()):
        favorites[uid] = [i for i in favorites[uid] if str(i) != str(listing_id)] if isinstance(favorites[uid], list) else []
    save_json(FILES["listings"], listings)
    save_json(FILES["messages"], messages)
    save_json(FILES["favorites"], favorites)
    return redirect(url_for("admin_panel"))


@app.route("/admin/user/toggle/<user_id>", methods=["POST"])
@admin_required
def admin_toggle_user(user_id):
    user = get_user_by_id(user_id)
    if not user:
        return "کاربر پیدا نشد.", 404
    if is_admin(user):
        return "مدیر قابل مسدود کردن نیست.", 400
    user["disabled"] = not user.get("disabled", False)
    save_json(FILES["users"], users)
    create_notification(user.get("id"), "وضعیت حساب تغییر کرد", "وضعیت حساب شما توسط مدیریت تغییر کرد.", url_for("settings"))
    return redirect(url_for("admin_panel"))


@app.route("/admin/report/resolve/<report_id>", methods=["POST"])
@admin_required
def admin_resolve_report(report_id):
    for report in reports:
        if str(report.get("id")) == str(report_id):
            report["status"] = "resolved"
            report["resolved_at"] = now_text()
            report["resolved_by"] = current_user().get("id")
            save_json(FILES["reports"], reports)
            return redirect(url_for("admin_panel"))
    return "گزارش پیدا نشد.", 404


@app.route("/admin/analytics")
@admin_required
def admin_analytics():
    by_cat = {}
    for x in listings:
        c = x.get("category", "سایر")
        by_cat[c] = by_cat.get(c, 0) + 1
    top = sorted(listings, key=lambda x: int(x.get("views", 0) or 0), reverse=True)[:10]
    total_views = sum(int(x.get("views", 0) or 0) for x in listings)
    active_count = sum(1 for x in listings if active_for_public(x))
    expired_count = sum(1 for x in listings if x.get("status") == "expired")
    sold_count = sum(1 for x in listings if x.get("status") == "sold")
    body = """<div class='card'><h1>📊 آمار کالا مود</h1><div class='statgrid'><div class='stat'><b>{{lc}}</b>کل آگهی</div><div class='stat'><b>{{active}}</b>فعال</div><div class='stat'><b>{{expired}}</b>منقضی</div><div class='stat'><b>{{sold}}</b>فروخته‌شده</div><div class='stat'><b>{{views}}</b>کل بازدید</div><div class='stat'><b>{{users}}</b>کاربر</div><div class='stat'><b>{{reports}}</b>گزارش</div><div class='stat'><b>{{featured}}</b>ویژه فعال</div></div></div><div class='card'><h2>📁 آمار دسته‌بندی</h2>{% for c,n in categories_stats %}<div class='box'><b>{{c}}</b><span style='float:left'>{{n}}</span></div>{% endfor %}</div><div class='card'><h2>🔥 ۱۰ آگهی پربازدید</h2>{% for x in top %}<div class='box'><b>#{{x.id}} — {{x.title}}</b><div class='small'>{{x.views}} بازدید · {{x.category}}</div></div>{% else %}<p>هنوز آماری وجود ندارد.</p>{% endfor %}</div><a class='btn light' href='{{url_for("admin_panel")}}'>بازگشت به مدیریت</a>"""
    return page("آمار مدیریت", body, user=current_user(), categories_stats=sorted(by_cat.items(), key=lambda x:x[1], reverse=True), top=top, lc=len(listings), active=active_count, expired=expired_count, sold=sold_count, views=total_views, users=len(users), reports=len(reports), featured=sum(1 for x in listings if featured_active(x)))


@app.route("/admin/backup")
@admin_required
def admin_backup():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"kalamod_backup_{stamp}.zip"
    backup_path = os.path.join(BASE_DIR, backup_name)
    try:
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as z:
            for path in FILES.values():
                if os.path.exists(path):
                    z.write(path, arcname=os.path.basename(path))
            if os.path.isdir(UPLOAD_FOLDER):
                for root, _, files in os.walk(UPLOAD_FOLDER):
                    for filename in files:
                        full = os.path.join(root, filename)
                        z.write(full, arcname=os.path.join("uploads", filename))
        return send_from_directory(BASE_DIR, backup_name, as_attachment=True, download_name=backup_name)
    except Exception as exc:
        return f"ساخت پشتیبان ناموفق بود: {exc}", 500


@app.route("/api/listings")
def api_listings():
    q = request.args.get("q", "").strip().lower()
    cat = request.args.get("category", "همه").strip()
    sort = request.args.get("sort", "newest").strip()
    public = []
    for x in listings:
        if not active_for_public(x):
            continue
        if q and q not in (str(x.get("title", "")) + " " + str(x.get("description", ""))).lower():
            continue
        if cat and cat != "همه" and x.get("category") != cat:
            continue
        public.append(x)
    if sort == "popular":
        public.sort(key=lambda x: int(x.get("views", 0) or 0), reverse=True)
    elif sort == "cheapest":
        public.sort(key=lambda x: price_number(x.get("price", "")))
    elif sort == "expensive":
        public.sort(key=lambda x: price_number(x.get("price", "")), reverse=True)
    elif sort == "featured":
        public.sort(key=lambda x: (featured_active(x), x.get("created_at", "")), reverse=True)
    else:
        public.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    data = []
    for x in public[:100]:
        data.append({"id": x.get("id"), "title": x.get("title", ""), "category": x.get("category", "سایر"), "price": x.get("price", "توافقی"), "views": int(x.get("views", 0) or 0), "featured": featured_active(x), "created_at": x.get("created_at", "")})
    return {"status": "ok", "count": len(data), "listings": data}


@app.route("/api/health")
def api_health():
    return {"status": "ok", "app": "kala-mod", "time": iso_now(), "listings": len(listings), "users": len(users)}


@app.errorhandler(400)
def bad_request(err):
    return page("درخواست نامعتبر", f"<div class='card center'><h1>درخواست نامعتبر</h1><p>{getattr(err, 'description', 'اطلاعات ارسال‌شده معتبر نیست.')}</p><a class='btn blue' href='{url_for("home")}'>بازگشت به خانه</a></div>", user=current_user()), 400


@app.errorhandler(404)
def not_found(_):
    return page("صفحه پیدا نشد", "<div class='card center'><h1>۴۰۴</h1><p>صفحه‌ای که می‌خواهی پیدا نشد.</p><a class='btn blue' href='{{url_for(\"home\")}}'>بازگشت به خانه</a></div>", user=current_user()), 404


@app.errorhandler(500)
def server_error(_):
    return page("خطای داخلی", "<div class='card center'><h1>خطای داخلی</h1><p>یک خطای موقت در برنامه رخ داد.</p><a class='btn blue' href='{{url_for(\"home\")}}'>بازگشت به خانه</a></div>", user=current_user()), 500


@app.errorhandler(413)
def too_large(_):
    return "حجم فایل بیشتر از ۸ مگابایت است.", 413



# ===== مرحله نهایی: امنیت، قوانین، PWA و آماده‌سازی انتشار =====
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)

_LOGIN_ATTEMPTS = {}
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_ATTEMPTS = 8


def login_rate_limited(ip):
    now = datetime.now().timestamp()
    row = _LOGIN_ATTEMPTS.get(ip, [])
    row = [t for t in row if now - t < LOGIN_WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[ip] = row
    return len(row) >= LOGIN_MAX_ATTEMPTS


def record_login_failure(ip):
    now = datetime.now().timestamp()
    row = _LOGIN_ATTEMPTS.get(ip, [])
    row = [t for t in row if now - t < LOGIN_WINDOW_SECONDS]
    row.append(now)
    _LOGIN_ATTEMPTS[ip] = row


def clear_login_failures(ip):
    _LOGIN_ATTEMPTS.pop(ip, None)


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cache-Control", "no-store") if request.path in {"/login", "/register", "/settings", "/password"} else None
    return response


@app.route("/manifest.webmanifest")
def manifest():
    return {
        "name": "کالا مود",
        "short_name": "کالا مود",
        "lang": "fa",
        "dir": "rtl",
        "start_url": "/",
        "display": "standalone",
        "description": "بازار آنلاین خرید و فروش کالا",
        "theme_color": "#111827",
        "background_color": "#f4f6f8",
        "icons": []
    }


@app.route("/service-worker.js")
def service_worker():
    js = """const CACHE='kalamod-v1';\nself.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.add('/'))));\nself.addEventListener('fetch',e=>{if(e.request.method==='GET')e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request).then(x=>{const y=x.clone();caches.open(CACHE).then(c=>c.put(e.request,y));return x}).catch(()=>caches.match('/'))));});"""
    return app.response_class(js, mimetype="application/javascript")


@app.route("/privacy")
def privacy():
    body = """<div class='card'><h1>🔒 حریم خصوصی</h1><p>کالا مود اطلاعاتی را که برای ایجاد حساب، ثبت آگهی، علاقه‌مندی، گفتگو، گزارش و پشتیبانی لازم است در داده‌های برنامه نگهداری می‌کند.</p><p>اطلاعات حساب و محتوای آگهی فقط برای عملکرد سرویس استفاده می‌شود و مدیر برنامه برای رسیدگی به گزارش‌ها و پشتیبانی به داده‌های لازم دسترسی دارد.</p><p>برای حذف حساب می‌توانی از بخش تنظیمات استفاده کنی.</p></div><div class='card'><a class='btn blue' href='{{url_for("home")}}'>بازگشت به خانه</a></div>"""
    return page("حریم خصوصی", body)


@app.route("/terms")
def terms():
    body = """<div class='card'><h1>📜 قوانین استفاده</h1><ul><li>ثبت آگهی باید با اطلاعات واقعی و مرتبط با کالا باشد.</li><li>محتوای توهین‌آمیز، کلاهبرداری، محتوای غیرقانونی و آگهی‌های خلاف قوانین مجاز نیست.</li><li>کاربران باید از اطلاعات شخصی دیگران سوءاستفاده نکنند.</li><li>آگهی‌های گزارش‌شده ممکن است توسط مدیر بررسی و در صورت نیاز غیرفعال شوند.</li><li>کالا مود برای امنیت کاربران، حق حذف یا محدودکردن محتوای خلاف قوانین را دارد.</li></ul></div><div class='card'><a class='btn blue' href='{{url_for("home")}}'>بازگشت به خانه</a></div>"""
    return page("قوانین استفاده", body)


@app.route("/about")
def about():
    body = """<div class='card center'><h1>کالا مود</h1><p>بازار آنلاین خرید و فروش کالا</p><p class='small'>نسخه آزمایشی محلی — آماده تکمیل برای انتشار</p><div class='actions' style='justify-content:center'><a class='btn light' href='{{url_for("privacy")}}'>حریم خصوصی</a><a class='btn light' href='{{url_for("terms")}}'>قوانین</a><a class='btn blue' href='{{url_for("support")}}'>پشتیبانی</a></div></div>"""
    return page("درباره کالا مود", body)

if __name__ == "__main__":
    print("=" * 55)
    print("کالا مود - نسخه کامل در حال اجراست")
    print("آدرس: http://127.0.0.1:5000")
    print("مدیر: admin")
    print("ویژگی‌ها: جستجوی پیشرفته | ویژه | نردبان | بازدید | گزارش | اعلان | مدیریت")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5000, debug=False)
