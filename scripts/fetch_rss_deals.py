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
MAX_DEALS = 80
REQUEST_TIMEOUT = 10
REQUEST_RETRIES = 2
RATE_LIMIT_DELAY = 0.5

# RSS feeds - existing + Slickdeals
RSS_FEEDS = [
    # Slickdeals
    "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
    "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1",
    # Reddit
    "https://www.reddit.com/r/buildapcsales/.rss",
    "https://www.reddit.com/r/deals/.rss",
    "https://www.reddit.com/r/AmazonDeals/.rss",
    # Deal news sites
    "https://www.dealnews.com/?rss=1",
    # Amazon deal blogs (these link to blog posts, script fetches Amazon URLs from pages)
    "https://happydealhappyday.com/category/amazon-deals/feed/",
    "https://moneysavingmom.com/category/deals/amazon-deals/feed/",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}

MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0"
    )
}

# ---- REGEX & HELPERS ----
AMAZON_LINK_REGEX = r'(https?://(?:www\.)?amazon\.com/[^\s"<>\']+)'
ASIN_REGEX = r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})"
PRICE_REGEX = r"\$([0-9,]+(?:\.[0-9]{1,2})?)"
DISCOUNT_REGEX = r"(\d+)%"


def amazon_image_from_asin(asin):
    """Constructs a higher-quality image URL from an ASIN."""
    if not asin:
        return None
    return f"https://m.media-amazon.com/images/I/{asin}._AC_SL1500_.jpg"


def add_affiliate_tag(url):
    """Adds our affiliate tag to a URL, preserving existing query params."""
    if not url or "amazon.com" not in url:
        return url
    # Clean URL and add affiliate tag
    base_url = url.split('?')[0] if '?' in url else url
    parsed = urlparse(base_url)
    q = {"tag": AFFILIATE_TAG}
    return urlunparse(parsed._replace(query=urlencode(q)))


def extract_amazon_url(text, fallback=""):
    """Finds an Amazon URL in the text or uses fallback."""
    if fallback and "amazon.com" in fallback:
        return fallback
    if not text:
        return None
    matches = re.findall(AMAZON_LINK_REGEX, text)
    for match in matches:
        if "amazon.com" in match:
            return match
    return matches[0] if matches else None


def extract_asin(url):
    """Extracts the 10-digit ASIN from an Amazon URL."""
    if not url:
        return None
    m = re.search(ASIN_REGEX, url)
    return m.group(1) if m else None


def extract_price_from_title(title):
    """Fallback to extract a price like $XX.XX from the title string."""
    if not title:
        return None
    m = re.search(PRICE_REGEX, title)
    if m:
        return f"${m.group(1).replace(',', '')}"
    return None


def extract_discount_from_title(title):
    """Extract discount percentage from title."""
    if not title:
        return 0
    m = re.search(DISCOUNT_REGEX, title)
    return int(m.group(1)) if m else 0


def extract_image_from_summary(summary):
    """Extracts the first image URL from HTML summary content."""
    if not summary:
        return None
    soup = BeautifulSoup(summary, "html.parser")
    img = soup.find("img")
    return img["src"] if img and img.get("src") else None


def clean_title(title):
    """Removes common clutter from deal titles."""
    if not title:
        return ""
    # Remove bracketed tags like [GPU] or [Amazon Deal]
    title = re.sub(r'\[.*?\]', '', title)
    # Remove parenthesized discounts like (50% OFF)
    title = re.sub(r'\(\s*\d+%\s*OFF\s*\)', '', title, flags=re.IGNORECASE)
    # Remove trailing price info
    title = re.sub(r'-\s*\$\d+(\.\d{2})?', '', title)
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
            price_val = float(price.replace('$', '').replace(',', ''))
            if price_val <= 10:
                score += 3
            elif price_val <= 25:
                score += 2
            elif price_val <= 50:
                score += 1
        except:
            pass
    # Discount attractiveness
    if discount >= 50:
        score += 3
    elif discount >= 30:
        score += 2
    elif discount >= 20:
        score += 1
    # Title quality indicators
    title_lower = title.lower()
    if any(word in title_lower for word in ['new', 'hot', 'limited', 'exclusive']):
        score += 1
    return score


def is_duplicate_title(title1, title2, threshold=0.85):
    """Check if two titles are duplicates using similarity matching."""
    return SequenceMatcher(None, title1.lower(), title2.lower()).ratio() >= threshold


def scrape_amazon_details(asin, title_price_fallback):
    """Return fallback data for speed."""
    return {
        "price": title_price_fallback or "$?",
        "rating": "N/A",
        "reviews": "N/A"
    }


def categorize(title):
    """Enhanced categorization with more categories and better matching."""
    t = title.lower()

    if any(x in t for x in ["gaming", "nintendo", "playstation", "xbox", "steam", "game", "controller", "joystick"]):
        return "Gaming"
    if any(x in t for x in ["ssd", "hdd", "nvme", "gpu", "graphics card", "ram", "memory", "keyboard", "mouse", "monitor", "router", "ipad", "iphone", "tablet", "laptop", "pc case", "cable", "charger", "headphones", "earbuds", "usb", "computer", "wifi"]):
        return "Tech"
    if any(x in t for x in ["sofa", "mattress", "vacuum", "kitchen", "cookware", "air purifier", "heater", "humidifier", "purifier", "bulb", "lamp", "blanket", "towel", "appliance"]):
        return "Home"
    if any(x in t for x in ["shoe", "fitness", "yoga", "treadmill", "dumbbell", "protein", "sneakers", "running", "workout", "gym", "exercise", "bike"]):
        return "Fitness"
    if any(x in t for x in ["case", "charger", "backpack", "watch band", "wallet", "sleeve", "stand", "mount", "holder"]):
        return "Accessories"
    if any(x in t for x in ["gift", "holiday", "christmas", "present", "lego", "toy", "puzzle", "board game", "card game"]):
        return "Gifts"
    if any(x in t for x in ["baby", "kids", "children", "diaper", "stroller", "crib", "educational"]):
        return "Baby"
    if any(x in t for x in ["shampoo", "conditioner", "soap", "lotion", "perfume", "makeup", "skincare", "hair", "nail"]):
        return "Beauty"
    if any(x in t for x in ["dog", "cat", "pet", "puppy", "kitten", "collar", "leash"]):
        return "Pets"

    return "General"


def fetch_amazon_url_from_page(page_url):
    """Try to fetch Amazon URL from a deal page (for Slickdeals etc)."""
    try:
        time.sleep(RATE_LIMIT_DELAY)
        response = requests.get(page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response.raise_for_status()

        # Look for Amazon URLs in page content
        amazon_url = extract_amazon_url(response.text)
        if amazon_url and extract_asin(amazon_url):
            return amazon_url

        # Parse and look for Amazon links
        soup = BeautifulSoup(response.text, "html.parser")
        for link in soup.find_all('a', href=True):
            href = link['href']
            if 'amazon.com' in href and ('/dp/' in href or '/gp/' in href):
                return href

        return None
    except:
        return None


def fetch_feed(url):
    """Fetch RSS feed with better error handling."""
    print(f"\n[RSS] Fetching: {url}")
    for attempt in range(REQUEST_RETRIES):
        try:
            time.sleep(RATE_LIMIT_DELAY)
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            feed = feedparser.parse(response.text)
            print(f"[RSS] Found {len(feed.entries)} entries")
            return feed.entries
        except Exception as e:
            print(f"[RSS] ERROR (Attempt {attempt+1}/{REQUEST_RETRIES}) -> {e}")
            time.sleep(2 ** attempt)
    return []


def main():
    """Main scraping function with enhanced duplicate removal."""
    deals = []
    seen_asins = set()
    seen_titles = set()
    category_counts = defaultdict(int)
    max_per_category = 25

    for feed_url in RSS_FEEDS:
        if len(deals) >= MAX_DEALS:
            print("[MAIN] Max deals reached, stopping early.")
            break

        entries = fetch_feed(feed_url)

        for entry in entries:
            if len(deals) >= MAX_DEALS:
                break

            title = entry.get("title", "").strip()
            summary = entry.get("summary", "")
            fallback_link = entry.get("link", "")

            # Try to find Amazon URL in summary or link
            amazon_url = extract_amazon_url(summary, fallback_link)

            # If no direct Amazon URL, try fetching from the page for known deal sites
            if not amazon_url:
                combined = f"{title} {summary}".lower()
                # Check if it's a deal site that links to blog posts (not direct Amazon links)
                deal_blog_domains = [
                    "slickdeals.net",
                    "moneysavingmom.com",
                    "happydealhappyday.com",
                    "dealnews.com",
                ]
                is_deal_blog = any(domain in fallback_link for domain in deal_blog_domains)

                if is_deal_blog and "amazon" in combined:
                    amazon_url = fetch_amazon_url_from_page(fallback_link)

            if not amazon_url:
                continue

            asin = extract_asin(amazon_url)
            if not asin or asin in seen_asins:
                continue

            # Check for title duplicates
            cleaned_title = clean_title(title)
            is_duplicate = any(is_duplicate_title(cleaned_title, seen) for seen in seen_titles)
            if is_duplicate:
                continue

            # Check category limits
            category = categorize(title)
            if category_counts[category] >= max_per_category:
                continue

            # Get image
            image = extract_image_from_summary(summary) or amazon_image_from_asin(asin)
            if not image:
                continue

            # Extract discount and price
            discount = extract_discount_from_title(title)
            title_price_fallback = extract_price_from_title(title)
            details = scrape_amazon_details(asin, title_price_fallback)

            # Calculate deal quality score
            score = calculate_deal_score(cleaned_title, details["price"], discount)

            # Build final URL with affiliate tag
            final_url = add_affiliate_tag(amazon_url)

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

    # Sort by quality score and recency
    deals.sort(key=lambda x: (x['score'], x['timestamp']), reverse=True)

    print(f"\n[TOTAL] Found {len(deals)} valid Amazon deals.")
    print("[CATEGORY BREAKDOWN]:")
    for cat, count in sorted(category_counts.items()):
        print(f"  {cat}: {count} deals")

    # Write to file
    with open(OUTPUT, "w") as f:
        json.dump({"deals": deals, "updated": int(time.time())}, f, indent=2)

    print(f"[DONE] Saved {len(deals)} deals to {OUTPUT}")


if __name__ == "__main__":
    main()
