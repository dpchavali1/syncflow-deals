import feedparser
import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

OUTPUT = "deals.json"
AFFILIATE_TAG = "syncflow-20"
MAX_DEALS = 120

RSS_FEEDS = [
    "https://slickdeals.net/newsearch.php?searcharea=deals&searchin=first&rss=1",
    "https://slickdeals.net/deals/?rss=1",
    "https://www.reddit.com/r/buildapcsales/.rss",
    "https://www.reddit.com/r/AmazonDeals/.rss",
    "https://www.reddit.com/r/Frugal/.rss",
    "https://www.dealnews.com/dealnews.xml",
]

IMG_REGEX = r"(https:\/\/[^\"\'\s]+?\.(?:jpg|jpeg|png|webp))"


def add_affiliate(url):
    if "amazon.com" not in url:
        return None

    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    q["tag"] = AFFILIATE_TAG

    return urlunparse(parsed._replace(query=urlencode(q, doseq=True)))


def extract_image(html):
    soup = BeautifulSoup(html, "html.parser")

    img = soup.find("img")
    if img and img.get("src"):
        return img["src"]

    m = re.search(IMG_REGEX, html)
    if m:
        return m.group(1)

    return None


def parse_feed(feed_url):
    print(f"[RSS] Fetching {feed_url}")

    feed = feedparser.parse(feed_url)
    deals = []

    for entry in feed.entries:
        title = entry.get("title", "").strip()
        summary = entry.get("summary", "")
        link = entry.get("link", "")

        if "amazon.com" not in link:
            continue

        aff_link = add_affiliate(link)
        if not aff_link:
            continue

        image = extract_image(summary)
        if not image:
            continue

        deals.append({
            "title": title,
            "image": image,
            "price": "$?",
            "url": aff_link,
            "category": "General"
        })

        if len(deals) >= MAX_DEALS:
            break

    print(f"[RSS] Found {len(deals)} deals")
    return deals


def main():
    all_deals = []

    for feed in RSS_FEEDS:
        all_deals.extend(parse_feed(feed))
        if len(all_deals) >= MAX_DEALS:
            break

    print(f"[TOTAL] Final deals count: {len(all_deals)}")

    final_json = {"deals": all_deals[:MAX_DEALS]}

    with open(OUTPUT, "w") as f:
        json.dump(final_json, f, indent=2)

    print(f"[DONE] Saved → {OUTPUT}")


if __name__ == "__main__":
    main()
