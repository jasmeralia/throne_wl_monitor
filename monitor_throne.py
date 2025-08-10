#!/usr/bin/env python3
import os
import re
import sys
import json
import time
import hashlib
import smtplib
import sqlite3
import random
import logging
import datetime
import pytz
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from tenacity import retry, wait_exponential_jitter, stop_after_attempt
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

DEBUG_DUMP_HTML = os.getenv("DEBUG_DUMP_HTML", "true").lower() == "true"
DEBUG_LOG_SAMPLES = os.getenv("DEBUG_LOG_SAMPLES", "true").lower() == "true"

def extract_items_jsonld(html: str):
    r"""Try to parse <script type="application/ld+json"> looking for ItemList."""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(s.string or "{}")
        except Exception:
            continue
        # Normalize array or object
        candidates = data if isinstance(data, list) else [data]
        for d in candidates:
            if not isinstance(d, dict):
                continue
            if d.get("@type") in ("ItemList", "Collection") or "itemListElement" in d:
                elements = d.get("itemListElement", [])
                for el in elements:
                    # el can be {"@type":"ListItem","item":{...}}
                    node = el.get("item", el) if isinstance(el, dict) else el
                    if not isinstance(node, dict):
                        continue
                    name = node.get("name") or node.get("title") or ""
                    url = node.get("url") or node.get("@id") or ""
                    image = node.get("image") or ""
                    offers = node.get("offers") or {}
                    currency = offers.get("priceCurrency") or "USD"
                    price = offers.get("price") or offers.get("priceAmount")
                    price_cents = -1
                    if price is not None:
                        try:
                            price_cents = int(round(float(str(price).replace(",", ".").strip())*100))
                        except Exception:
                            price_cents = -1
                    key = url or name
                    if not key:
                        continue
                    item_id = hashlib.sha1(key.encode()).hexdigest()
                    items.append({
                        "item_id": item_id,
                        "name": str(name).strip(),
                        "price_cents": price_cents,
                        "currency": currency,
                        "product_url": url,
                        "image_url": image if isinstance(image, str) else (image[0] if isinstance(image, list) and image else ""),
                        "available": 1,
                    })
    # Dedup
    uniq = {it["item_id"]: it for it in items}
    return list(uniq.values())

def extract_items_jsonld(html: str):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        # Normalize to list
        data_list = data if isinstance(data, list) else [data]
        for d in data_list:
            # Look for ItemList or Product entries
            if d.get("@type") == "ItemList" and isinstance(d.get("itemListElement"), list):
                for el in d["itemListElement"]:
                    item = el.get("item") if isinstance(el, dict) else el
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name") or ""
                    url = item.get("url") or ""
                    # offers may be dict or list
                    offers = item.get("offers")
                    price_cents = -1
                    currency = "USD"
                    if isinstance(offers, dict):
                        price = offers.get("price")
                        currency = offers.get("priceCurrency") or currency
                        try:
                            if price is not None:
                                price_cents = int(round(float(str(price))*100))
                        except Exception:
                            pass
                    elif isinstance(offers, list) and offers:
                        off = offers[0]
                        price = off.get("price")
                        currency = off.get("priceCurrency") or currency
                        try:
                            if price is not None:
                                price_cents = int(round(float(str(price))*100))
                        except Exception:
                            pass
                    item_id = item.get("@id") or (url and hashlib.sha1(url.encode()).hexdigest())
                    out.append({
                        "item_id": str(item_id) if item_id else hashlib.sha1((name+url).encode()).hexdigest(),
                        "name": name.strip(),
                        "price_cents": price_cents,
                        "currency": currency,
                        "product_url": url or "",
                        "image_url": (item.get("image") or ""),
                        "available": 1,
                    })
    # Deduplicate by item_id
    uniq = {}
    for c in out:
        uniq[c["item_id"]] = c
    return list(uniq.values())

def extract_items_grid(html: str):
    soup = BeautifulSoup(html, "lxml")
    items = []
    price_re = re.compile(r"(?<!\w)([$€£])\s?([0-9]+(?:[.,][0-9]{2})?)")
    # Find all anchors that look like gift cards (exclude header/footer/nav)
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        if not txt or len(txt) < 3:
            continue
        # Skip obvious nav/login links
        lower = txt.lower()
        if any(k in lower for k in ("login","sign up","about","contact","faq","feature requests","how it works","follow","wishlist","gifters")):
            continue
        # Try to find a nearby price within the same card block (ancestor within 3 levels)
        price_cents = -1
        currency = "USD"
        container = a
        found_price = None
        for _ in range(4):
            if container is None:
                break
            text_block = container.get_text(" ", strip=True)
            m = price_re.search(text_block or "")
            if m:
                symbol, num = m.groups()
                if symbol == "€":
                    currency = "EUR"
                elif symbol == "£":
                    currency = "GBP"
                try:
                    price_cents = int(round(float(num.replace(",", "."))*100))
                except Exception:
                    price_cents = -1
                found_price = True
                break
            container = container.parent
        if not found_price:
            continue
        href = a["href"]
        # Normalize throne-relative links
        if href.startswith("/"):
            href = "https://throne.com" + href
        item_id = hashlib.sha1(href.encode()).hexdigest() if href else hashlib.sha1(txt.encode()).hexdigest()
        items.append({
            "item_id": item_id,
            "name": txt,
            "price_cents": price_cents,
            "currency": currency,
            "product_url": href or "",
            "image_url": "",
            "available": 1,
        })
    # Dedup by name+url to reduce noise
    uniq = {}
    for it in items:
        key = (it["product_url"], it["name"])
        uniq[key] = it
    return list(uniq.values())

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("throne-monitor")

# File logging (rotating) to /data by default so logs persist on TrueNAS SCALE
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() == "true"
LOG_FILE = os.getenv("LOG_FILE", "/data/monitor.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(2 * 1024 * 1024)))  # 2MB
LOG_BACKUPS = int(os.getenv("LOG_BACKUPS", "3"))
try:
    if LOG_TO_FILE:
        from logging.handlers import RotatingFileHandler
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS)
        fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(fh)
except Exception as _e:
    logger.warning("Failed to initialize file logging: %s", _e)

STATE_DB = os.getenv("STATE_DB", "/data/state.sqlite3")
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "10"))
MODE = os.getenv("MODE", "daemon")
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
PROXY_URL = os.getenv("PROXY_URL", "").strip()
DEBUG_DUMP_HTML = os.getenv("DEBUG_DUMP_HTML", "true").lower() == "true"
DEBUG_LOG_SAMPLES = os.getenv("DEBUG_LOG_SAMPLES", "true").lower() == "true"

EMAIL_TO = os.getenv("EMAIL_TO", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

THRONE_TARGETS = [t.strip() for t in os.getenv("THRONE_TARGETS", "").split(",") if t.strip()]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
if PROXY_URL:
    SESSION.proxies.update({"http": PROXY_URL, "https": PROXY_URL})


def _sanitize_filename(url: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9._-]+', '_', url)
    return safe[:150]

def _debug_dump_html(url: str, html: str):
    if not DEBUG_DUMP_HTML:
        return
    try:
        dbg_dir = os.path.join(os.path.dirname(STATE_DB), "debug")
        os.makedirs(dbg_dir, exist_ok=True)
        fname = _sanitize_filename(url) + ".html"
        fpath = os.path.join(dbg_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(html)
        logger.warning("Saved debug HTML to %s", fpath)
    except Exception as e:
        logger.warning("Failed to save debug HTML: %s", e)

def ensure_db():
    os.makedirs(os.path.dirname(STATE_DB), exist_ok=True)
    with sqlite3.connect(STATE_DB) as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS items (
            wishlist_id TEXT,
            item_id TEXT,
            name TEXT,
            price_cents INTEGER,
            currency TEXT,
            product_url TEXT,
            image_url TEXT,
            available INTEGER,
            first_seen TEXT,
            last_seen TEXT,
            PRIMARY KEY (wishlist_id, item_id)
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            wishlist_id TEXT,
            event_type TEXT,   -- added|removed|price_change
            item_id TEXT,
            name TEXT,
            from_price_cents INTEGER,
            to_price_cents INTEGER
        )""")
        con.commit()

def normalize_target(t: str) -> str:
    # Accept username or full URL
    if t.startswith("http://") or t.startswith("https://"):
        return t
    return f"https://throne.com/u/{t}/wishlist"

def extract_items_next_data(html: str):
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return None
    try:
        data = json.loads(script.string)
    except Exception:
        return None

    items = []

    def deep_iter(node, path=""):
        nonlocal items
        if isinstance(node, dict):
            if "items" in node and isinstance(node["items"], list):
                maybe = node["items"]
                if any(isinstance(x, dict) and ("name" in x or "title" in x) for x in maybe):
                    items = maybe
            for k, v in node.items():
                deep_iter(v, path + f".{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                deep_iter(v, path + f"[{i}]")

    deep_iter(data)

    if not items:
        return None

    normalized = []
    for it in items:
        name = it.get("name") or it.get("title") or ""
        price = it.get("price") or it.get("price_cents") or it.get("priceCents")
        currency = it.get("currency") or it.get("currencyCode") or "USD"
        url = it.get("url") or it.get("productUrl") or it.get("url_path") or ""
        image = it.get("image") or it.get("imageUrl") or ""
        item_id = it.get("id") or it.get("uuid") or (url and hashlib.sha1(url.encode()).hexdigest())
        avail = it.get("available")
        if avail is None:
            avail = 1
        price_cents = None
        if isinstance(price, int):
            price_cents = price
        elif isinstance(price, (float, str)):
            try:
                price_cents = int(round(float(str(price).replace("$","").strip())*100))
            except Exception:
                price_cents = None

        normalized.append({
            "item_id": str(item_id) if item_id else hashlib.sha1((name+url).encode()).hexdigest(),
            "name": str(name).strip(),
            "price_cents": price_cents if price_cents is not None else -1,
            "currency": currency,
            "product_url": url,
            "image_url": image,
            "available": 1 if bool(avail) else 0,
        })
    return normalized

def extract_items_html(html: str):
    soup = BeautifulSoup(html, "lxml")

    candidates = []
    for card in soup.select("[class*='card'],[class*='Card'],[data-testid*='item'],article,li"):
        text = card.get_text(" ", strip=True)
        if not text or len(text) < 3:
            continue
        m = re.search(r"(?<!\w)([\\$€£])\\s?([0-9]+(?:[\\.,][0-9]{2})?)", text)
        currency = "USD"
        price_cents = -1
        if m:
            symbol, num = m.group(1), m.group(2).replace(",", ".")
            if symbol == "€":
                currency = "EUR"
            elif symbol == "£":
                currency = "GBP"
            try:
                price_cents = int(round(float(num)*100))
            except Exception:
                price_cents = -1
        name_el = card.select_one("h3,h2,.title,[class*='title']")
        name = name_el.get_text(" ", strip=True) if name_el else text[:120]

        link = None
        a = card.find("a", href=True)
        if a:
            link = a["href"]
            if link.startswith("/"):
                link = "https://throne.com" + link

        img = None
        imgel = card.find("img")
        if imgel and imgel.get("src"):
            img = imgel["src"]

        key = link or name
        item_id = hashlib.sha1(key.encode()).hexdigest()

        candidates.append({
            "item_id": item_id,
            "name": name,
            "price_cents": price_cents,
            "currency": currency,
            "product_url": link or "",
            "image_url": img or "",
            "available": 1,
        })

    uniq = {}
    for c in candidates:
        uniq[c["item_id"]] = c
    return list(uniq.values())

from tenacity import RetryError

@retry(wait=wait_exponential_jitter(initial=1, max=30), stop=stop_after_attempt(5))
def fetch(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def get_items_for_target(target: str):
    url = normalize_target(target)
    html = fetch(url)
    items = extract_items_next_data(html)
    if items is None or len(items) == 0:
        logger.debug("NEXT_DATA extraction failed or empty; trying JSON-LD")
        jitems = extract_items_jsonld(html)
        if jitems:
            items = jitems
    if not items:
        logger.debug("JSON-LD extraction failed; falling back to HTML parsing")
        items = extract_items_html(html)
    # If still zero, optionally dump HTML for debugging
    if (not items) and DEBUG_DUMP_HTML:
        try:
            os.makedirs("/data/debug", exist_ok=True)
            # create safe filename from url
            safe = re.sub(r'[^a-zA-Z0-9_.-]+', '_', url)
            fname = f"/data/debug/{safe}.html"
            with open(fname, "w", encoding="utf-8") as f:
                f.write(html)
            logger.warning("Parsed 0 items for %s. Saved HTML to %s for inspection.", url, fname)
        except Exception as e:
            logger.warning("Failed to dump HTML for %s: %s", url, e)
    else:
        if DEBUG_LOG_SAMPLES:
            sample = items[:3]
            logger.debug("Sample parsed items for %s: %s", url, sample)
    logger.info("Found %d items for %s", len(items) if items else 0, url)
    return url, (items or [])

def now_utc_iso():
    return datetime.datetime.now(tz=pytz.UTC).isoformat()

def diff_and_store(wishlist_id: str, items: list):
    ts = now_utc_iso()
    with sqlite3.connect(STATE_DB) as con:
        cur = con.cursor()
        cur.execute("SELECT item_id, name, price_cents FROM items WHERE wishlist_id=?", (wishlist_id,))
        prev = {row[0]: {"name": row[1], "price_cents": row[2]} for row in cur.fetchall()}
        current_ids = set()

        added, removed, price_changes = [], [], []

        for it in items:
            item_id = it["item_id"]
            current_ids.add(item_id)
            cur.execute("""
                INSERT INTO items (wishlist_id,item_id,name,price_cents,currency,product_url,image_url,available,first_seen,last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(wishlist_id,item_id) DO UPDATE SET
                    name=excluded.name,
                    price_cents=excluded.price_cents,
                    currency=excluded.currency,
                    product_url=excluded.product_url,
                    image_url=excluded.image_url,
                    available=excluded.available,
                    last_seen=excluded.last_seen
            """, (wishlist_id, item_id, it["name"], it["price_cents"], it["currency"],
                  it["product_url"], it["image_url"], it["available"], ts, ts))

            if item_id not in prev:
                added.append(it)
                cur.execute("INSERT INTO events (ts,wishlist_id,event_type,item_id,name,from_price_cents,to_price_cents) VALUES (?,?,?,?,?,?,?)",
                            (ts, wishlist_id, "added", item_id, it["name"], None, it["price_cents"]))
            else:
                before = prev[item_id]["price_cents"]
                after = it["price_cents"]
                if before is not None and after is not None and before != after and before >= 0 and after >= 0:
                    price_changes.append((it, before, after))
                    cur.execute("INSERT INTO events (ts,wishlist_id,event_type,item_id,name,from_price_cents,to_price_cents) VALUES (?,?,?,?,?,?,?)",
                                (ts, wishlist_id, "price_change", item_id, it["name"], before, after))

        removed_ids = set(prev.keys()) - current_ids
        for rid in removed_ids:
            name = prev[rid]["name"]
            removed.append({"item_id": rid, "name": name})
            cur.execute("INSERT INTO events (ts,wishlist_id,event_type,item_id,name,from_price_cents,to_price_cents) VALUES (?,?,?,?,?,?,?)",
                        (ts, wishlist_id, "removed", rid, name, None, None))

        con.commit()

    return added, removed, price_changes

def cents_to_str(cents: int, currency: str = "USD") -> str:
    if cents is None or cents < 0:
        return "unknown"
    sym = "$" if currency == "USD" else ("€" if currency == "EUR" else ("£" if currency == "GBP" else ""))
    return f"{sym}{cents/100:.2f}" if sym else f"{cents/100:.2f} {currency}"

def send_email(subject: str, body: str):
    if not (EMAIL_TO and EMAIL_FROM and SMTP_HOST):
        logger.warning("Email not configured; skipping email: %s", subject)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    msg.attach(MIMEText(body, "plain"))

    if SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
    try:
        if not SMTP_USE_SSL:
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass

def summarize_changes(wishlist_id: str, added, removed, price_changes):
    lines = [f"Wishlist: {wishlist_id}"]
    if added:
        lines.append("\nAdded:")
        for it in added:
            lines.append(f"  • {it['name']}  ({cents_to_str(it['price_cents'], it.get('currency','USD'))})  {it.get('product_url','')}")
    if removed:
        lines.append("\nRemoved:")
        for it in removed:
            lines.append(f"  • {it['name']}")
    if price_changes:
        lines.append("\nPrice changes:")
        for it, before, after in price_changes:
            lines.append(f"  • {it['name']}: {cents_to_str(before, it.get('currency','USD'))} → {cents_to_str(after, it.get('currency','USD'))}")
    return "\n".join(lines)

def jitter_sleep(minutes: int):
    base = max(1, minutes)
    jitter = random.uniform(-0.1*base, 0.1*base)
    total = base + jitter
    time.sleep(total * 60)

def run_once():
    ensure_db()
    targets = THRONE_TARGETS
    if not targets:
        logger.error("No THRONE_TARGETS configured.")
        return 1

    any_changes = False

    for t in targets:
        try:
            wishlist_id, items = get_items_for_target(t)
            added, removed, price_changes = diff_and_store(wishlist_id, items)
            if added or removed or price_changes:
                any_changes = True
                subject = f"[Throne] Changes detected for {wishlist_id}"
                body = summarize_changes(wishlist_id, added, removed, price_changes)
                send_email(subject, body)
                logger.info("Email sent for %s", wishlist_id)
            else:
                logger.info("No changes for %s", wishlist_id)
        except Exception as e:
            logger.exception("Failed processing %s: %s", t, e)

    return 0 if not (MODE == "once" and any_changes is False) else 0

def run_daemon():
    logger.info("Starting daemon; poll every %d minutes", POLL_MINUTES)
    while True:
        try:
            run_once()
        except Exception as e:
            logger.exception("Unhandled error in run_once: %s", e)
        jitter_sleep(POLL_MINUTES)

if __name__ == "__main__":
    try:
        if MODE == "once":
            sys.exit(run_once())
        else:
            sys.exit(run_daemon())
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        sys.exit(2)
