import feedparser
import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

AFFILIATE_TAG = "syncflow-20"
OUTPUT_FILE = "deals.json"
LIMIT = 100

AMAZON_FEEDS = [
    "https://www.amazon.com/gp/goldbox?format=rss",
    "https://www.amazon.com/Best-Sellers/zgbs?format=rss",
    "https://www.amazon.com/Most-Wished-For/zgbs?format=rss",
]

IMAGE_PATTERN = r"(https:\/\/[^\"\'\s]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\"\'\s]*)?)"


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


def extract_image_from_html(html):
    soup = BeautifulSoup(html, "html.parser")

    img_tag = soup.find("img")
    if img_tag and img_tag.get("src"):
        return img_tag.get("src")

    match = re.search(IMAGE_PATTERN, html)
    if match:
        return match.group(1)

    return None


def clean_title(t):
    return re.sub(r"[\n\r\t]+", " ", t).strip()


def fetch_deals():
    deals = []

    for feed_url in AMAZON_FEEDS:
        feed = feedparser.parse(feed_url)

        for entry in feed.entries:
            title = clean_title(entry.title)
            link = affiliate(entry.link)

            image = extract_image_from_html(entry.summary)
            if not image:
                continue

            deal = {
                "title": title,
                "image": upscale(image),
                "price": "$?",    # Can add price extraction later
                "url": link,
                "category": "Tech"
            }
            deals.append(deal)

            if len(deals) >= LIMIT:
                return deals

    return deals


def save_json(deals):
    output = {"deals": deals}
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)


if __name__ == "__main__":
    deals = fetch_deals()
    save_json(deals)
    print(f"Generated {len(deals)} deals.")
