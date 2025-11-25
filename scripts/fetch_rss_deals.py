import feedparser
import json
import re
import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

# ---- CONFIG ----
OUTPUT = "deals.json"
AFFILIATE_TAG = "syncflow-20"
MAX_DEALS = 80
REQUEST_TIMEOUT = 15
REQUEST_RETRIES = 2

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

MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/15.0 Mobile/15E148 Safari/604.1"
    )
}

# ---- REGEX & HELPERS ----
AMAZON_LINK_REGEX = r'(https:\/\/(?:www\.)?amazon\.com\/[^\s"']+)'
ASIN_REGEX = r"([A-Z0-9]{10})"
PRICE_REGEX = r"(\$[0-9]+(?:\.[0-9]{1,2})?)"

def amazon_image_from_asin(asin):
    """Constructs a higher-quality image URL from an ASIN."""
    if not asin:
        return None
    return f"https://m.media-amazon.com/images/I/{asin}._AC_SL1500_.jpg"

def add_affiliate_tag(url):
    """Adds our affiliate tag to a URL, preserving existing query params."""
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    q["tag"] = AFFILIATE_TAG
    return urlunparse(parsed._replace(query=urlencode(q, doseq=True)))

def extract_amazon_url(summary, fallback):
    """Finds an Amazon URL in the summary text, otherwise uses the fallback link."""
    if "amazon.com" in fallback:
        return fallback
    m = re.search(AMAZON_LINK_REGEX, summary)
    return m.group(1) if m else None

def extract_asin(url):
    """Extracts the 10-digit ASIN from an Amazon URL."""
    m = re.search(ASIN_REGEX, url)
    return m.group(1) if m else None

def extract_price_from_title(title):
    """Fallback to extract a price like $XX.XX from the title string."""
    m = re.search(PRICE_REGEX, title)
    return m.group(1) if m else None

def extract_image_from_summary(summary):
    """Extracts the first image URL from HTML summary content."""
    soup = BeautifulSoup(summary, "html.parser")
    img = soup.find("img")
    return img["src"] if img and img.get("src") else None

def clean_title(title):
    """Removes common clutter from deal titles."""
    # Remove bracketed tags like [GPU] or [Amazon Deal]
    title = re.sub(r'\[.*?\]', '', title)
    # Remove parenthesized discounts like (50% OFF)
    title = re.sub(r'\(\s*\d+%\s*OFF\s*\)', '', title, flags=re.IGNORECASE)
    # Remove trailing price info
    title = re.sub(r'-\s*\$\d+(\.\d{2})?$', '', title)
    # Collapse whitespace
    title = re.sub(r'\s+', ' ', title).strip()
    return title

# ---- AMAZON PAGE SCRAPER ----
def scrape_amazon_details(asin, title_price_fallback):
    """
    Scrapes Amazon mobile page for price, rating, and review count.
    This is brittle and will break. It tries multiple selectors for price.
    """
    mobile_url = f"https://m.amazon.com/dp/{asin}"

    try:
        page = requests.get(mobile_url, headers=MOBILE_HEADERS, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(page.text, "html.parser")

        # --- PRICE ---
        price = None
        # Common price element selectors for Amazon's mobile site
        price_selectors = [
            '#corePrice_feature_div .a-price .a-offscreen',
            '#price_inside_buybox',
            '#price',
            'span.a-price[data-a-size="xl"] span.a-offscreen'
        ]
        for selector in price_selectors:
            price_element = soup.select_one(selector)
            if price_element:
                price = price_element.get_text(strip=True)
                break
        
        # If scraping fails, try the title fallback
        if not price or price == "$?":
             price = title_price_fallback

        # --- RATING ---
        rating = "N/A"
        rating_span = soup.find("span", string=re.compile(r"out of 5"))
        if rating_span:
            rating = rating_span.get_text(strip=True)

        # --- REVIEW COUNT ---
        reviews = "N/A"
        # Find the link that contains the review count text
        review_link = soup.select_one('#acrCustomerReviewText')
        if review_link:
            reviews = review_link.get_text(strip=True)

        return {
            "price": price or "$?",
            "rating": rating,
            "reviews": reviews
        }

    except Exception as e:
        print(f"[SCRAPER] ERROR scraping ASIN {asin}: {e}")
        return {
            "price": title_price_fallback or "$?",
            "rating": "N/A",
            "reviews": "N/A"
        }

# ---- CATEGORIZATION ----
def categorize(title):
    t = title.lower()
    if any(x in t for x in ["ssd", "gpu", "ram", "keyboard", "mouse", "router", "ipad", "iphone", "tablet", "laptop", "monitor", "pc case", "cable"]):
        return "Tech"
    if any(x in t for x in ["sofa", "mattress", "vacuum", "kitchen", "cookware", "air purifier", "heater", "humidifier", "purifier", "bulb"]):
        return "Home"
    if any(x in t for x in ["shoe", "fitness", "yoga", "treadmill", "dumbbell", "protein", "sneakers"]):
        return "Fitness"
    if any(x in t for x in ["case", "charger", "backpack", "watch band", "wallet"]):
        return "Accessories"
    if any(x in t for x in ["gift", "holiday", "christmas", "present", "lego"]):
        return "Gifts"
    return "General"


# ---- MAIN SCRIPT ----
def fetch_feed(url):
    print(f"\n[RSS] Fetching: {url}")
    for attempt in range(REQUEST_RETRIES):
        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            feed = feedparser.parse(response.text)
            print(f"[RSS] Found {len(feed.entries)} entries")
            return feed.entries
        except Exception as e:
            print(f"[RSS] ERROR (Attempt {attempt+1}/{REQUEST_RETRIES}) → {e}")
            time.sleep(1)
    return []

def main():
    deals = []
    seen_asins = set()

    for feed_url in RSS_FEEDS:
        if len(deals) >= MAX_DEALS:
            print("[MAIN] Max deals reached, stopping early.")
            break
        
        entries = fetch_feed(feed_url)

        for entry in entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "")
            fallback_link = entry.get("link", "")

            amazon_url = extract_amazon_url(summary, fallback_link)
            if not amazon_url:
                continue

            asin = extract_asin(amazon_url)
            if not asin or asin in seen_asins:
                continue

            # Get image, prefer from summary
            image = extract_image_from_summary(summary) or amazon_image_from_asin(asin)
            if not image:
                print(f"[SKIP] No image for ASIN {asin}")
                continue

            # Fallback for price from title, then scrape for details
            title_price_fallback = extract_price_from_title(title)
            details = scrape_amazon_details(asin, title_price_fallback)

            # Clean up the deal
            cleaned_title = clean_title(title)
            final_url = add_affiliate_tag(amazon_url)
            category = categorize(cleaned_title)
            
            # Add to list
            deals.append({
                "id": asin, # ADDED: Use ASIN as the unique ID
                "title": cleaned_title,
                "image": image,
                "price": details["price"],
                "rating": details["rating"],
                "reviews": details["reviews"],
                "url": final_url,
                "category": category,
                "timestamp": int(time.time())
            })
            seen_asins.add(asin)
            print(f"[DEAL] Added: {cleaned_title} ({details['price']})")

            if len(deals) >= MAX_DEALS:
                break

    print(f"\n[TOTAL] Found {len(deals)} valid Amazon deals.")

    # Write to file
    with open(OUTPUT, "w") as f:
        json.dump({"deals": deals}, f, indent=2)

    print(f"[DONE] Saved {len(deals)} deals to {OUTPUT}")

if __name__ == "__main__":
    main()
