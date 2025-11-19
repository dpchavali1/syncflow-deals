import feedparser
import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36"
}

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


def fetch_feed(url):
    print(f"[RSS] Fetching: {url}")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        content = resp.text
        feed = feedparser.parse(content)

        print(f"[RSS] Entries found: {len(feed.entries)}")
        return feed.entries
    except Exception as e:
        print(f"[RSS] ERROR: {e}")
        return []


def main():
    all_deals = []

    for feed_url in RSS_FEEDS:
        entries = fetch_feed(feed_url)

        for entry in entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "")
            link = entry.get("link", "")

            # Keep ONLY Amazon deals
            if "amazon.com" not in link:
                continue

            aff_link = add_affiliate(link)
            if not aff_link:
                continue

            # Extract image
            image = extract_image(summary)
            if not image:
                continue

            all_deals.append({
                "title": title,
                "image": image,
                "price": "$?",    # optional placeholder
                "url": aff_link,
                "category": "General"
            })

            if len(all_deals) >= MAX_DEALS:
                break

        if len(all_deals) >= MAX_DEALS:
            break

    print(f"[TOTAL] Final deals count: {len(all_deals)}")

    final_json = {"deals": all_deals}

    with open(OUTPUT, "w") as f:
        json.dump(final_json, f, indent=2)

    print(f"[DONE] Saved → {OUTPUT}")


if __name__ == "__main__":
    main()
