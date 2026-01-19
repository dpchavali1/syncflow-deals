import feedparser
import json
import re
import requests
import time
import hashlib
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
from difflib import SequenceMatcher
from collections import defaultdict
# ---- CONFIG ----
OUTPUT = "deals.json"
AFFILIATE_TAG = "syncflow-20"
MAX_DEALS = 80  # Optimized for faster execution
REQUEST_TIMEOUT = 10  # Faster timeout
REQUEST_RETRIES = 2   # Fewer retries
RATE_LIMIT_DELAY = 0.5  # Faster scraping
# Prioritized RSS feeds (most reliable first)
RSS_FEEDS = [
    # High-quality, fast sources first
    "https://slickdeals.net/newsearch.php?searchin=first&rss=1",
    "https://www.reddit.com/r/buildapcsales/.rss",
    "https://www.reddit.com/r/deals/.rss",
    "https://www.dealnews.com/?rss=1",
    # Medium priority
    "https://happydealhappyday.com/category/amazon-deals/feed/",
    "https://www.dealsplus.com/rss.xml",
    "https://www.techbargains.com/rss.xml",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0 Safari/537.36"
    )
}
MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0"
    )
}
# ---- REGEX & HELPERS ----
AMAZON_LINK_REGEX = r'(https://www\.amazon\.com/[^\s"<]+)'
ASIN_REGEX = r"([A-Z0-9]{10})"
PRICE_REGEX = r"(\$[0-9]+(?:\.[0-9]{1,2})?)"
DISCOUNT_REGEX = r"(\d+)%"
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
def extract_discount_from_title(title):
    """Extract discount percentage from title."""
    m = re.search(DISCOUNT_REGEX, title)
    return int(m.group(1)) if m else 0
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
    # Remove common deal site prefixes
    title = re.sub(r'^(Deal:|Hot Deal:|Amazon Deal:)', '', title, flags=re.IGNORECASE)
    # Collapse whitespace
    title = re.sub(r'\s+', ' ', title).strip()
    return title
def calculate_deal_score(title, price, discount):
    """Calculate a quality score for the deal."""
    score = 0
    # Price attractiveness (lower price = higher score)
    if price:
        try:
            price_val = float(price.replace('$', ''))
            if price_val <= 10: score += 3
            elif price_val <= 25: score += 2
            elif price_val <= 50: score += 1
        except:
            pass
    # Discount attractiveness
    if discount >= 50: score += 3
    elif discount >= 30: score += 2
    elif discount >= 20: score += 1
    # Title quality indicators
    title_lower = title.lower()
    if any(word in title_lower for word in ['new', 'hot', 'limited', 'exclusive']):
        score += 1
    return score
def is_duplicate_title(title1, title2, threshold=0.85):
    """Check if two titles are duplicates using similarity matching."""
    return SequenceMatcher(None, title1.lower(), title2.lower()).ratio() >= threshold
# ---- AMAZON PAGE SCRAPER (Optional - skip for speed) ----
def scrape_amazon_details(asin, title_price_fallback):
    """
    Simplified: Just return fallback data for speed.
    In production, you can re-enable full scraping.
    """
    # For speed, skip Amazon scraping and just use title fallback
    return {
        "price": title_price_fallback or "$?",
        "rating": "N/A",
        "reviews": "N/A"
    }
# ---- CATEGORIZATION ----
def categorize(title):
    """Enhanced categorization with more categories and better matching."""
    t = title.lower()
    # Tech categories
    if any(x in t for x in ["ssd", "hdd", "nvme", "gpu", "graphics card", "ram", "memory", "keyboard", "mouse", "monitor", "router", "ipad", "iphone", "tablet", "laptop", "pc case", "cable", "charger", "headphones", "earbuds"]):
        return "Tech"
    # Gaming specific
    if any(x in t for x in ["gaming", "nintendo", "playstation", "xbox", "steam", "game", "controller", "joystick"]):
        return "Gaming"
    # Home & Kitchen
    if any(x in t for x in ["sofa", "mattress", "vacuum", "kitchen", "cookware", "air purifier", "heater", "humidifier", "purifier", "bulb", "lamp", "blanket", "towel", "appliance"]):
        return "Home"
    # Fitness & Sports
    if any(x in t for x in ["shoe", "fitness", "yoga", "treadmill", "dumbbell", "protein", "sneakers", "running", "workout", "gym", "exercise", "bike"]):
        return "Fitness"
    # Accessories
    if any(x in t for x in ["case", "charger", "backpack", "watch band", "wallet", "sleeve", "stand", "mount", "holder"]):
        return "Accessories"
    # Gifts & Toys
    if any(x in t for x in ["gift", "holiday", "christmas", "present", "lego", "toy", "puzzle", "board game", "card game"]):
        return "Gifts"
    # Baby & Kids
    if any(x in t for x in ["baby", "kids", "children", "diaper", "stroller", "crib", "toy", "educational"]):
        return "Baby"
    # Beauty & Personal Care
    if any(x in t for x in ["shampoo", "conditioner", "soap", "lotion", "perfume", "makeup", "skincare", "hair", "nail"]):
        return "Beauty"
    # Pet Supplies
    if any(x in t for x in ["dog", "cat", "pet", "puppy", "kitten", "collar", "leash", "bed", "food", "treat"]):
        return "Pets"
    return "General"
# ---- MAIN SCRIPT ----
def fetch_feed(url):
    """Fetch RSS feed with better error handling."""
    print(f"\n[RSS] Fetching: {url}")
    for attempt in range(REQUEST_RETRIES):
        try:
            time.sleep(RATE_LIMIT_DELAY)  # Rate limiting
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            feed = feedparser.parse(response.text)
            print(f"[RSS] Found {len(feed.entries)} entries")
            return feed.entries
        except Exception as e:
            print(f"[RSS] ERROR (Attempt {attempt+1}/{REQUEST_RETRIES}) → {e}")
            time.sleep(2 ** attempt)  # Exponential backoff
    return []
def main():
    """Main scraping function with enhanced duplicate removal."""
    deals = []
    seen_asins = set()
    seen_titles = set()
    # Track deals by category for better distribution
    category_counts = defaultdict(int)
    max_per_category = 25  # Limit deals per category
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
            # Check for title duplicates
            is_duplicate = any(is_duplicate_title(title, seen) for seen in seen_titles)
            if is_duplicate:
                print(f"[SKIP] Duplicate title: {title[:50]}...")
                continue
            # Check category limits
            category = categorize(title)
            if category_counts[category] >= max_per_category:
                continue
            # Get image, prefer from summary
            image = extract_image_from_summary(summary) or amazon_image_from_asin(asin)
            if not image:
                print(f"[SKIP] No image for ASIN {asin}")
                continue
            # Extract discount for scoring
            discount = extract_discount_from_title(title)
            # Fallback for price from title, then scrape for details
            title_price_fallback = extract_price_from_title(title)
            details = scrape_amazon_details(asin, title_price_fallback)
            # Calculate deal quality score
            score = calculate_deal_score(title, details["price"], discount)
            # Clean up the deal
            cleaned_title = clean_title(title)
            final_url = add_affiliate_tag(amazon_url)
            # Add to list
            deal = {
                "id": asin,
                "title": cleaned_title,
                "image": image,
                "price": details["price"],
                "rating": details["rating"],
                "reviews": details["reviews"],
                "url": final_url,
                "category": category,
                "timestamp": int(time.time()),
                "score": score,
                "discount": discount
            }
            deals.append(deal)
            seen_asins.add(asin)
            seen_titles.add(cleaned_title)
            category_counts[category] += 1
            print(f"[DEAL] Added: {cleaned_title[:50]}... ({details['price']}) - {category}")
            if len(deals) >= MAX_DEALS:
                break
    # Sort by quality score and recency
    deals.sort(key=lambda x: (x['score'], x['timestamp']), reverse=True)
    print(f"\n[TOTAL] Found {len(deals)} valid Amazon deals.")
    print("[CATEGORY BREAKDOWN]:")
    for cat, count in sorted(category_counts.items()):
        print(f"  {cat}: {count} deals")
    # Write to file
    with open(OUTPUT, "w") as f:
        json.dump({"deals": deals}, f, indent=2)
    print(f"[DONE] Saved {len(deals)} deals to {OUTPUT}")
if __name__ == "__main__":
    main()
