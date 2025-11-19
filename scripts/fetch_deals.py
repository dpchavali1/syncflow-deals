import json
import requests
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

AFFILIATE_TAG = "syncflow-20"
OUTPUT_FILE = "deals.json"
LIMIT = 100

API_URL = "https://api.storeradar.io/amazon/daily-deals"


def affiliate(url):
    if "tag=" in url:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["tag"] = AFFILIATE_TAG
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def upscale(img_url):
    return f"https://wsrv.nl/?url={img_url}&w=600&h=400&fit=cover"


def fetch_deals():
    print("[DEALS] Fetching new Amazon deals ...")
    resp = requests.get(API_URL, timeout=20)

    if resp.status_code != 200:
        print("[DEALS] ERROR: Bad response", resp.status_code)
        return []

    data = resp.json()
    items = data.get("deals", [])

    print(f"[DEALS] Received {len(items)} items.")

    deals = []

    for item in items[:LIMIT]:
        title = item.get("title", "").strip()
        price = item.get("price", "$?")
        url = affiliate(item.get("url", ""))
        img = item.get("image", "")

        if not img or not title or not url:
            continue

        deal = {
            "title": title,
            "image": upscale(img),
            "price": price,
            "url": url,
            "category": "Tech"
        }

        deals.append(deal)

    print(f"[DEALS] Final deals count: {len(deals)}")
    return deals


def save_json(deals):
    output = {"deals": deals}
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)


if __name__ == "__main__":
    deals = fetch_deals()
    save_json(deals)
    print(f"Generated {len(deals)} deals.")
