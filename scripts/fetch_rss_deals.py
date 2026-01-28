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
CACHE_FILE = "feed_cache.json"
AFFILIATE_TAG = "syncflow-20"
MAX_DEALS = 80
REQUEST_TIMEOUT = 10
REQUEST_RETRIES = 2
RATE_LIMIT_DELAY = 0.8  # Slightly slower to avoid rate limiting when fetching images
DEBUG = True  # Set to True to see why entries are skipped

# RSS feeds - existing + Slickdeals
RSS_FEEDS = [
    # Slickdeals
    "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
    "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1",
    # Reddit
    "https://www.reddit.com/r/buildapcsales/.rss",
    "https://www.reddit.com/r/deals/.rss",
    "https://www.reddit.com/r/amazondeals/.rss",  # lowercase
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
    """Fallback: construct Amazon product page URL to scrape image later."""
    # Note: This doesn't actually work as Amazon images use different IDs
    # We'll fetch the real image from the product page
    if not asin:
        return None
    return None  # Return None to force fetching from page


def fetch_amazon_image(asin):
    """Fetch the actual product image from Amazon product page."""
    if not asin:
        return None

    try:
        url = f"https://www.amazon.com/dp/{asin}"
        time.sleep(RATE_LIMIT_DELAY)
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Try multiple selectors for product image
        image_selectors = [
            '#landingImage',
            '#imgBlkFront',
            '#ebooksImgBlkFront',
            '.a-dynamic-image',
            '#main-image',
            'img[data-old-hires]',
            '#imageBlock img',
        ]

        for selector in image_selectors:
            img = soup.select_one(selector)
            if img:
                # Try data-old-hires first (high res), then src
                img_url = img.get('data-old-hires') or img.get('data-a-dynamic-image') or img.get('src')
                if img_url:
                    # Handle data-a-dynamic-image which is JSON
                    if img_url.startswith('{'):
                        try:
                            import json as json_module
                            img_data = json_module.loads(img_url)
                            img_url = list(img_data.keys())[0] if img_data else None
                        except:
                            continue
                    if img_url and img_url.startswith('http') and 'amazon.com' in img_url or 'media-amazon.com' in img_url:
                        return img_url

        # Try Open Graph image as fallback
        og_image = soup.select_one('meta[property="og:image"]')
        if og_image and og_image.get('content'):
            return og_image['content']

        return None
    except Exception as e:
        print(f"[IMAGE] Error fetching image for {asin}: {e}")
        return None


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
    """Extracts the best image URL from HTML summary content."""
    if not summary:
        return None
    soup = BeautifulSoup(summary, "html.parser")

    # Look for Amazon images first (they're higher quality)
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if src and ("amazon.com" in src or "media-amazon.com" in src):
            # Clean up and get higher res version
            src = re.sub(r'\._[A-Z]{2}\d+_', '._AC_SL500_', src)
            return src

    # Then look for any reasonable image
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if src and src.startswith("http"):
            # Skip tiny icons and tracking pixels
            width = img.get("width", "100")
            height = img.get("height", "100")
            try:
                if int(width) < 50 or int(height) < 50:
                    continue
            except:
                pass
            # Skip common non-product images
            if any(skip in src.lower() for skip in ['icon', 'logo', 'avatar', 'pixel', 'tracking', 'badge']):
                continue
            return src

    return None


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
    """Try to fetch Amazon URL and image from a deal page (for Slickdeals etc).
    Returns tuple: (amazon_url, image_url)
    """
    try:
        time.sleep(RATE_LIMIT_DELAY)
        response = requests.get(page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        amazon_url = None
        image_url = None

        # Look for Amazon URLs in anchor tags first (more reliable)
        for link in soup.find_all('a', href=True):
            href = link['href']
            # Check various Amazon URL patterns
            if 'amazon.com' in href:
                if '/dp/' in href or '/gp/' in href or '/product/' in href:
                    if extract_asin(href):
                        amazon_url = href
                        break
            # Also check for amzn.to short links
            elif 'amzn.to' in href or 'amzn.com' in href:
                # Try to follow the redirect to get real Amazon URL
                try:
                    redirect_resp = requests.head(href, headers=HEADERS, timeout=5, allow_redirects=True)
                    final_url = redirect_resp.url
                    if 'amazon.com' in final_url and extract_asin(final_url):
                        amazon_url = final_url
                        break
                except:
                    pass

        # Fallback: look via regex in full page content
        if not amazon_url or not extract_asin(amazon_url):
            amazon_url = extract_amazon_url(response.text)

        # Extract image from the page (usually deal blogs have product images)
        image_url = extract_image_from_summary(str(soup))

        if DEBUG and amazon_url:
            print(f"[DEBUG] Found Amazon URL: {amazon_url[:60]}...")

        return (amazon_url, image_url)
    except Exception as e:
        print(f"[FETCH] Error fetching {page_url}: {e}")
        return (None, None)


def load_feed_cache():
    """Load feed cache from file."""
    try:
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}


def save_feed_cache(cache):
    """Save feed cache to file."""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[CACHE] Error saving cache: {e}")


def fetch_feed(url, cache=None):
    """Fetch RSS feed with caching and better error handling."""
    print(f"\n[RSS] Fetching: {url}")

    # Check cache for ETag/Last-Modified
    cached_etag = None
    cached_modified = None
    if cache and url in cache:
        cached_etag = cache[url].get('etag')
        cached_modified = cache[url].get('last_modified')

    for attempt in range(REQUEST_RETRIES):
        try:
            time.sleep(RATE_LIMIT_DELAY)

            # Add conditional headers if we have cached data
            req_headers = HEADERS.copy()
            if cached_etag:
                req_headers['If-None-Match'] = cached_etag
            if cached_modified:
                req_headers['If-Modified-Since'] = cached_modified

            response = requests.get(url, headers=req_headers, timeout=REQUEST_TIMEOUT)

            # 304 = Not Modified, skip this feed
            if response.status_code == 304:
                print(f"[RSS] Not modified (cached), skipping")
                return []

            response.raise_for_status()

            # Update cache with new ETag/Last-Modified
            if cache is not None:
                cache[url] = {
                    'etag': response.headers.get('ETag'),
                    'last_modified': response.headers.get('Last-Modified'),
                    'fetched_at': int(time.time())
                }

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

    # Load feed cache
    feed_cache = load_feed_cache()

    for feed_url in RSS_FEEDS:
        if len(deals) >= MAX_DEALS:
            print("[MAIN] Max deals reached, stopping early.")
            break

        entries = fetch_feed(feed_url, feed_cache)

        for entry in entries:
            if len(deals) >= MAX_DEALS:
                break

            title = entry.get("title", "").strip()
            summary = entry.get("summary", "")
            fallback_link = entry.get("link", "")

            # Also check content:encoded (many RSS feeds put full content here)
            content_encoded = ""
            if hasattr(entry, 'content') and entry.content:
                content_encoded = entry.content[0].get('value', '') if entry.content else ""

            # Combine all text sources for searching
            all_content = f"{summary} {content_encoded}"

            # Try to find Amazon URL in summary, content, or link
            amazon_url = extract_amazon_url(all_content, fallback_link)
            page_image = None  # Image found from deal page

            # If no direct Amazon URL, try fetching from the page for known deal sites
            if not amazon_url:
                combined = f"{title} {all_content}".lower()
                # Check if it's a deal site that links to blog posts (not direct Amazon links)
                deal_blog_domains = [
                    "slickdeals.net",
                    "moneysavingmom.com",
                    "happydealhappyday.com",
                    "dealnews.com",
                ]
                is_deal_blog = any(domain in fallback_link for domain in deal_blog_domains)

                # Check for Amazon-related keywords
                amazon_keywords = ["amazon", "amzn", "prime deal", "shipped", "subscribe & save"]
                has_amazon_keyword = any(kw in combined for kw in amazon_keywords)

                # For deal blogs, always try to fetch the page (they often have Amazon deals)
                if is_deal_blog and (has_amazon_keyword or "deal" in title.lower()):
                    if DEBUG:
                        print(f"[DEBUG] Fetching page: {fallback_link[:60]}...")
                    amazon_url, page_image = fetch_amazon_url_from_page(fallback_link)

            if not amazon_url:
                if DEBUG and "amazon" in f"{title} {all_content}".lower():
                    print(f"[DEBUG] No Amazon URL found for: {title[:50]}...")
                continue

            asin = extract_asin(amazon_url)
            if not asin:
                if DEBUG:
                    print(f"[DEBUG] No ASIN in URL: {amazon_url[:60]}...")
                continue
            if asin in seen_asins:
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

            # Get image - try multiple sources in order of preference
            image = None

            # 1. Try image from RSS summary/content
            image = extract_image_from_summary(all_content)

            # 2. Try image from deal page (if we fetched it)
            if not image and page_image:
                image = page_image

            # 3. Fetch directly from Amazon product page
            if not image:
                print(f"[IMAGE] Fetching image from Amazon for {asin}...")
                image = fetch_amazon_image(asin)

            # If no image found, use a placeholder (don't skip the deal)
            if not image:
                if DEBUG:
                    print(f"[DEBUG] No image for: {cleaned_title[:40]}... using placeholder")
                # Use Amazon's generic product image placeholder
                image = f"https://m.media-amazon.com/images/I/{asin}._AC_SL500_.jpg"

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

    # Save feed cache
    save_feed_cache(feed_cache)

    # Write to file
    with open(OUTPUT, "w") as f:
        json.dump({"deals": deals, "updated": int(time.time())}, f, indent=2)

    print(f"[DONE] Saved {len(deals)} deals to {OUTPUT}")


if __name__ == "__main__":
    main()
