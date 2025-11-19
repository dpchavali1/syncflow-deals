import feedparser
import json
import re
import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

# ---- AMAZON PAGE SCRAPER ----
def scrape_amazon_details(asin):
    """
    Scrapes Amazon mobile page for price, rating, and review count.
    Uses m.amazon.com (mobile) because parsing is easier and less blocked.
    """
    url = f"https://www.amazon.com/dp/{asin}?psc=1"
    mobile_url = f"https://m.amazon.com/dp/{asin}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/15.0 Mobile/15E148 Safari/604.1"
        )
    }

    try:
        page = requests.get(mobile_url, headers=headers, timeout=12)
        soup = BeautifulSoup(page.text, "html.parser")

        # PRICE -----------------------------------------------------
        price = None
        price_span = soup.find("span", string=re.compile(r"\$"))
        if price_span:
            price = price_span.get_text(strip=True)

        # RATINGS ---------------------------------------------------
        rating = None
        rating_span = soup.find("span", string=re.compile(r"out of 5"))
        if rating_span:
            rating = rating_span.get_text(strip=True)

        # REVIEW COUNT ----------------------------------------------
        reviews = None
        review_span = soup.find("span", string=re.compile(r"ratings?"))
        if review_span:
            reviews = review_span.get_text(strip=True)

        return {
            "price": price or "$?",
            "rating": rating or "N/A",
            "reviews": reviews or "N/A"
        }

    except Exception as e:
        print(f"[SCRAPER] ERROR scraping ASIN {asin}: {e}")
        return {
            "price": "$?",
            "rating": "N/A",
            "reviews": "N/A"
        }

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

# Detect price in title
PRICE_REGEX = r"(\$[0-9]+(?:\.[0-9]{1,2})?)"

def amazon_image_from_asin(asin):
    if not asin:
        return None
    return f"https://m.media-amazon.com/images/I/{asin}._AC_SX679_.jpg"


def add_affiliate_tag(url):
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    if "tag" not in q:
        q["tag"] = AFFILIATE_TAG
    return urlunparse(parsed._replace(query=urlencode(q, doseq=True)))


def extract_amazon_url(summary, fallback):
    if "amazon.com" in fallback:
        return fallback
    m = re.search(AMAZON_LINK_REGEX, summary)
    if m:
        return m.group(1)
    return None


def extract_asin(url):
    m = re.search(ASIN_REGEX, url)
    return m.group(1) if m else None


def extract_price(title):
    m = re.search(PRICE_REGEX, title)
    return m.group(1) if m else "$?"


def extract_image(summary):
    soup = BeautifulSoup(summary, "html.parser")

    img = soup.find("img")
    if img and img.get("src"):
        return img["src"]

    return None


def fetch_feed(url, retries=3):
    print(f"\n[RSS] Fetching: {url}")
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            feed = feedparser.parse(response.text)
            print(f"[RSS] Entries: {len(feed.entries)}")
            return feed.entries
        except Exception as e:
            print(f"[RSS] ERROR ({attempt+1}/{retries}) → {e}")
            time.sleep(1)
    return []


def categorize(title):
    t = title.lower()

    if any(x in t for x in ["ssd", "gpu", "ram", "keyboard", "mouse", "router",
                            "ipad", "iphone", "tablet", "laptop", "monitor"]):
        return "Tech"

    if any(x in t for x in ["sofa", "mattress", "vacuum", "kitchen", "cookware",
                            "air purifier", "heater", "humidifier"]):
        return "Home"

    if any(x in t for x in ["shoe", "fitness", "yoga", "treadmill",
                            "dumbbell", "protein"]):
        return "Fitness"

    if any(x in t for x in ["case", "charger", "backpack", "watch band",
                            "wallet"]):
        return "Accessories"

    if any(x in t for x in ["gift", "holiday", "christmas", "present"]):
        return "Gifts"

    return "General"


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

            # Deduplicate
            if asin in seen_asins:
                continue
            seen_asins.add(asin)

            amazon_url = add_affiliate_tag(amazon_url)

            # Get image
            image = extract_image(summary)
            if not image:
                image = amazon_image_from_asin(asin)
            if not image:
                continue

            # Extract price
            details = scrape_amazon_details(asin)
            price = details["price"]
            rating = details["rating"]
            reviews = details["reviews"]


            deals.append({
                "title": title,
                "image": image,
                "price": price,
                "rating": rating,
                "reviews": reviews,
                "url": amazon_url,
                "category": categorize(title),
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
