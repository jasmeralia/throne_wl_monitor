# Throne Wishlist Monitor (Docker)

A lightweight container that watches public **Throne** wishlists and emails you when:
- **New items are added**
- **Items are removed**
- **Prices change**

It stores state in a SQLite DB (`/data/state.sqlite3`) so it can diff snapshots over time.

> Inspired by the amazon_wl_monitor concept, but tailored to Throne.

---

## Quick Start

```bash
# 1) Download this project
# 2) Edit docker-compose.yml with your details (targets + SMTP settings)
# 3) Build and run
docker compose up -d --build
```

- Default mode is a daemon that polls every `POLL_MINUTES` (default 10).
- Use `MODE=once` to run a single check then exit (handy for cron/K8s CronJob).

State persists under `./data/` on the host.

---

## Configuration

Edit `docker-compose.yml` environment section:

- `THRONE_TARGETS`: comma-separated list of **usernames** or full **wishlist URLs**  
  Examples: `morgan`, `https://throne.com/u/someone/wishlist`

- Email (SMTP): `EMAIL_TO`, `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`  
  - Set `SMTP_USE_SSL=true` for SMTPS/465. Otherwise STARTTLS is used.

- Behavior:  
  - `MODE=daemon|once`  
  - `POLL_MINUTES=10`  
  - `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR`

- Optional: `USER_AGENT`, `PROXY_URL`

- **Logging to file**: set `LOG_TO_FILE=true` (default) to write all logs to `/data/monitor.log`.  
  You can also adjust `LOG_FILE`, `LOG_MAX_BYTES`, and `LOG_BACKUPS` for rotation.

---

## What it collects

For each item (best-effort, based on Throne's public page structure):
- `item_id` (stable id if discoverable, otherwise a hash of the URL)
- `name`
- `price_cents`
- `currency`
- `product_url`
- `image_url` (if available)
- `available` (best guess)

The parser first attempts to extract JSON from the `__NEXT_DATA__` script used by many Next.js sites. If that fails, it falls back to HTML scraping of product cards. Selectors and keys are centralized in the code so you can tweak easily if Throne changes markup.

---

## Emails

When a change is detected for any monitored wishlist, you'll receive a summary email with sections:
- **Added items**
- **Removed items**
- **Price changes** (old → new)

If nothing changed, no email is sent.

---

## Multiple Wishlists

Set `THRONE_TARGETS` to multiple users/URLs separated by commas. Each wishlist is tracked independently.

---

## Using the prebuilt image from GHCR

You can skip building locally and pull the image directly from GitHub Container Registry:

```yaml
services:
  throne-monitor:
    image: ghcr.io/jasmeralia/throne_wl_monitor:latest   # or a specific tag like v1.0.0
    container_name: throne-monitor
    restart: unless-stopped
    environment:
      THRONE_TARGETS: "morgan,https://throne.com/u/someone/wishlist"
      EMAIL_TO: "you@example.com"
      EMAIL_FROM: "throne-monitor@yourdomain.com"
      SMTP_HOST: "smtp.yourdomain.com"
      SMTP_PORT: 587
      SMTP_USER: "throne-monitor@yourdomain.com"
      SMTP_PASS: "yourpassword"
      SMTP_USE_SSL: "false"
      MODE: "daemon"
      POLL_MINUTES: 10
      USER_AGENT: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
      PROXY_URL: ""
      LOG_LEVEL: "INFO"
      LOG_TO_FILE: "true"
      LOG_FILE: "/data/monitor.log"
      LOG_MAX_BYTES: 2097152
      LOG_BACKUPS: 3
    volumes:
      - ./data:/data
```

---

## License

MIT
