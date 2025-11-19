import feedparser
import json
import re
import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

OUTPUT = "deals.json"
AFFILIATE_TAG = "syncflow-20"
MAX_DEALS = 120

RSS_FEEDS = [
    # Slickdeals
    "https://slickdeals.net/newsearch.php?searcharea=deals&searchin=first&rss=1",
    "https://slickdeals.net/deals/?rss=1",

    # Reddit – Amazon & tech deals
    "https://www.reddit.com/r/AmazonDeals/.rss",
    "https://www.reddit.com/r/buildapcsales/.rss",
    "https://www.reddit.com/r/deals/.rss",
    "https://www.reddit.com/r/Frugal/.rss",

    # DealNews
    "https://www.dealnews.com/dealnews.xml",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    )
}

# ASIN patterns for all Amazon URL forms
ASIN_PATTERNS = [
    r"/dp/([A-Z0-9]{10})",
    r"/gp/product/([A-Z0-9]{10})",
    r"/product/([A-Z0-9]{10})",
    r"/([A-Z0-9]{10})(?:[/?]|$)"
]

# Clean Amazon URL → keep only dp/ASIN
def clean_amazon_url(url):
    asin = extract_asin(url)
    if not asin:
        return None
    return f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}"


def extract_asin(url):
    for pattern in ASIN_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def extract_raw_amazon(summary, fallback):
    if "amazon.com" in fallback:
        return fallback

    m = re.search(r"(https:\/\/(?:www\.)?amazon\.com\/[^\s\"']+)", summary)
    return m.group(1) if m else None


def fetch_feed(url):
    print(f"\n[RSS] Fetching: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        print(f"[RSS] Entries found: {len(feed.entries)}")
        return feed.entries
    except Exception as e:
        print(f"[RSS] ERROR: {e}")
        return []


# BEST image strategy: always use ASIN image
def amazon_image_from_asin(asin):
    return f"https://m.media-amazon.com/images/I/{asin}._AC_SX679_.jpg"


# Category classifier
def classify(title):
    t = title.lower()

    if any(x in t for x in ["ram", "ssd", "gpu", "graphics", "intel", "ryzen", "motherboard"]):
        return "PC Parts"

    if any(x in t for x in ["tv", "headphone", "earbud", "speaker", "soundbar"]):
        return "Electronics"

    if any(x in t for x in ["kitchen", "cookware", "blender", "pan", "air fryer"]):
        return "Home & Kitchen"

    if any(x in t for x in ["toy", "lego", "board game"]):
        return "Toys"

    if any(x in t for x in ["shirt", "jacket", "pajama", "slipper"]):
        return "Clothing"

    return "General"


def main():
    deals = []
    seen = set()

    for feed_url in RSS_FEEDS:
        entries = fetch_feed(feed_url)

        for e in entries:
            title = e.get("title", "").strip()
            summary = e.get("summary", "")
            link = e.get("link", "")

            raw_amazon = extract_raw_amazon(summary, link)
            if not raw_amazon:
                continue

            asin = extract_asin(raw_amazon)
            if not asin:
                continue

            if asin in seen:
                continue
            seen.add(asin)

            clean_url = clean_amazon_url(raw_amazon)
            image = amazon_image_from_asin(asin)

            category = classify(title)

            deals.append({
                "title": title,
                "image": image,
                "price": "$?",
                "url": clean_url,
                "category": category,
                "timestamp": int(time.time())
            })

            if len(deals) >= MAX_DEALS:
                break

        if len(deals) >= MAX_DEALS:
            break

    print(f"\n[TOTAL] Clean Amazon deals collected: {len(deals)}")

    with open(OUTPUT, "w") as f:
        json.dump({"deals": deals}, f, indent=2)

    print("[DONE] Saved → deals.json")


if __name__ == "__main__":
    main()
