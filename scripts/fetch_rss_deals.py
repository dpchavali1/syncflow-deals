import feedparser
import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

OUTPUT = "deals.json"
AFFILIATE_TAG = "syncflow-20"
MAX_DEALS = 80

RSS_FEEDS = [
    "https://slickdeals.net/newsearch.php?searcharea=deals&searchin=first&rss=1",
    "https://slickdeals.net/deals/?rss=1",
    "https://www.reddit.com/r/buildapcsales/.rss",
    "https://www.reddit.com/r/AmazonDeals/.rss",
    "https://www.reddit.com/r/Frugal/.rss",
    "https://www.dealnews.com/dealnews.xml",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0 Safari/537.36"
    )
}

AMAZON_URL_REGEX = r"(https:\/\/www\.amazon\.com\/[^\s\"']+)"
ASIN_REGEX = r"/dp/([A-Z0-9]{8,12})"
IMG_REGEX = r"(https:\/\/[^\"\'\s]+?\.(?:jpg|jpeg|png|webp))"


def add_affiliate(url):
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    q["tag"] = AFFILIATE_TAG
    return urlunparse(parsed._replace(query=urlencode(q, doseq=True)))


def extract_amazon_link(html, fallback_link):
    # 1) Direct link field
    if "amazon.com" in fallback_link:
        return fallback_link

    # 2) Try extract from HTML
    m = re.search(AMAZON_URL_REGEX, html)
    if m:
        return m.group(1)

    return None


def extract_image(html):
    soup = BeautifulSoup(html, "html.parser")

    # 1) direct img tag
    img = soup.find("img")
    if img and img.get("src"):
        return img["src"]

    # 2) regex fallback
    m = re.search(IMG_REGEX, html)
    if m:
        return m.group(1)

    return None


def asin_from_url(url):
    m = re.search(ASIN_REGEX, url)
    return m.group(1) if m else None


def fallback_amazon_image(asin):
    if not asin:
        return None
    return f"https://m.media-amazon.com/images/I/{asin}._AC_SX679_.jpg"


def fetch_feed(url):
    print(f"[RSS] Fetching: {url}")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        feed = feedparser.parse(resp.text)
        print(f"[RSS] Entries found: {len(feed.entries)}")
        return feed.entries
    except Exception as e:
        print(f"[RSS] ERROR: {e}")
        return []


def main():
    deals = []

    for feed_url in RSS_FEEDS:
        entries = fetch_feed(feed_url)

        for e in entries:
            title = e.get("title", "").strip()
            summary = e.get("summary", "")
            link = e.get("link", "")

            amazon_url = extract_amazon_link(summary, link)
            if not amazon_url:
                continue

            amazon_url = add_affiliate(amazon_url)
            asin = asin_from_url(amazon_url)

            # Get image or fallback
            image = extract_image(summary)
            if not image:
                image = fallback_amazon_image(asin)

            if not image:
                continue

            deals.append({
                "title": title,
                "image": image,
                "price": "$?",  # placeholder
                "url": amazon_url,
                "category": "General"
            })

            if len(deals) >= MAX_DEALS:
                break

        if len(deals) >= MAX_DEALS:
            break

    print(f"[TOTAL] Final deals count: {len(deals)}")

    with open(OUTPUT, "w") as f:
        json.dump({"deals": deals}, f, indent=2)

    print("[DONE] Saved → deals.json")


if __name__ == "__main__":
    main()
