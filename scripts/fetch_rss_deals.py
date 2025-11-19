import feedparser
import json
import re
import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

OUTPUT = "deals.json"
AFFILIATE_TAG = "syncflow-20"
MAX_DEALS = 80

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
    "https://www.dealnews.com/dealnews.xml"
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0 Safari/537.36"
    )
}

# Detect any Amazon link
AMAZON_LINK_REGEX = r"(https:\/\/(?:www\.)?amazon\.com\/[^\s\"']+)"

# Detect ASIN inside URL
ASIN_REGEX = r"([A-Z0-9]{10})"

# Fallback image from ASIN
def amazon_image_from_asin(asin):
    if not asin:
        return None
    return f"https://m.media-amazon.com/images/I/{asin}._AC_SX679_.jpg"


def add_affiliate_tag(url):
    if "tag=" in url:
        return url
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    q["tag"] = AFFILIATE_TAG
    return urlunparse(parsed._replace(query=urlencode(q, doseq=True)))


def extract_amazon_url(summary, fallback):
    # 1) If entry.link already contains amazon URL
    if "amazon.com" in fallback:
        return fallback

    # 2) Try find inside HTML content
    m = re.search(AMAZON_LINK_REGEX, summary)
    if m:
        return m.group(1)

    return None


def extract_asin(url):
    m = re.search(ASIN_REGEX, url)
    return m.group(1) if m else None


def extract_image(summary):
    soup = BeautifulSoup(summary, "html.parser")

    img = soup.find("img")
    if img and img.get("src"):
        return img["src"]

    return None


def fetch_feed(url):
    print(f"\n[RSS] Fetching: {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        print(f"[RSS] Entries: {len(feed.entries)}")
        return feed.entries
    except Exception as e:
        print(f"[RSS] ERROR fetching {url}: {e}")
        return []


def main():
    deals = []
    seen_asins = set()

    for feed_url in RSS_FEEDS:
        entries = fetch_feed(feed_url)

        for entry in entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "")
            fallback_link = entry.get("link", "")

            amazon_url = extract_amazon_url(summary, fallback_link)
            if not amazon_url:
                continue

            asin = extract_asin(amazon_url)
            if not asin:
                continue

            # Skip duplicates
            if asin in seen_asins:
                continue
            seen_asins.add(asin)

            amazon_url = add_affiliate_tag(amazon_url)

            image = extract_image(summary)
            if not image:
                image = amazon_image_from_asin(asin)

            if not image:
                continue

            deals.append({
                "title": title,
                "image": image,
                "price": "$?",
                "url": amazon_url,
                "category": "General",
                 "timestamp": int(time.time())
            })

            if len(deals) >= MAX_DEALS:
                break

        if len(deals) >= MAX_DEALS:
            break

    print(f"\n[TOTAL] Amazon deals found: {len(deals)}")

    with open(OUTPUT, "w") as f:
        json.dump({"deals": deals}, f, indent=2)

    print("[DONE] Saved to deals.json")


if __name__ == "__main__":
    main()
