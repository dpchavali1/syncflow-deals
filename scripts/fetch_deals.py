import feedparser
import json
import re
import requests
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

AFFILIATE_TAG = "syncflow-20"
OUTPUT_FILE = "deals.json"
LIMIT = 100

AMAZON_FEEDS = [
    "https://www.amazon.com/gp/goldbox?format=rss",
    "https://www.amazon.com/Best-Sellers/zgbs?format=rss",
    "https://www.amazon.com/Most-Wished-For/zgbs?format=rss",
]

# Regex to extract image
IMAGE_REGEX = r"(https:\/\/[^\s]+\.jpg)"

# Clean and attach affiliate tag
def affiliate(url):
    if "tag=" in url:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["tag"] = AFFILIATE_TAG
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))

# Upscale image using a free resizing proxy
def upscale(img_url):
    return f"https://wsrv.nl/?url={img_url}&w=600&h=400&fit=cover"

# Clean title
def clean_title(t):
    return re.sub(r"[\n\r\t]+", " ", t).strip()

# Extract first JPG
def extract_image(text):
    m = re.search(IMAGE_REGEX, text)
    return m.group(1) if m else None

def fetch_deals():
    deals = []

    for feed_url in AMAZON_FEEDS:
        feed = feedparser.parse(feed_url)

        for entry in feed.entries:
            title = clean_title(entry.title)
            link = affiliate(entry.link)

            image = extract_image(entry.summary) or ""
            if not image:
                continue  # skip if no image

            upscaled = upscale(image)

            deal = {
                "title": title,
                "image": upscaled,
                "price": "$?",            # RSS doesn't provide price
                "url": link,
                "category": "Tech"       # Default; can auto-detect later
            }

            deals.append(deal)

    # Limit results
    return deals[:LIMIT]

def save_json(deals):
    output = {"deals": deals}
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

if __name__ == "__main__":
    deals = fetch_deals()
    save_json(deals)
    print(f"Generated {len(deals)} deals.")
