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

# ---- SHARED CONFIG ----
CACHE_FILE = "feed_cache.json"
MAX_DEALS = 80
REQUEST_TIMEOUT = 10
REQUEST_RETRIES = 2
RATE_LIMIT_DELAY = 0.8  # Slightly slower to avoid rate limiting when fetching images
DEBUG = True  # Set to True to see why entries are skipped

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

# ---- REGION CONFIGS ----

REGION_US = {
    "name": "US",
    "output": "deals.json",
    "affiliate_tag": "syncflow-20",
    "amazon_domain": "amazon.com",
    "currency_symbol": "$",
    "price_regex": r"\$([0-9,]+(?:\.[0-9]{1,2})?)",
    "price_format": lambda p: f"${p}",
    "feeds": [
        # Slickdeals
        "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
        "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1",
        # Reddit
        "https://www.reddit.com/r/buildapcsales/.rss",
        "https://www.reddit.com/r/deals/.rss",
        "https://www.reddit.com/r/AmazonDeals/.rss",
        # Deal news sites
        "https://www.dealnews.com/?rss=1",
        # Amazon deal blogs
        "https://happydealhappyday.com/category/amazon-deals/feed/",
        "https://moneysavingmom.com/category/deals/amazon-deals/feed/",
    ],
    "deal_blog_domains": [
        "slickdeals.net",
        "moneysavingmom.com",
        "happydealhappyday.com",
        "dealnews.com",
    ],
    # Price thresholds for scoring (in USD)
    "price_thresholds": {"high_score": 10, "med_score": 25, "low_score": 50},
}

REGION_IN = {
    "name": "IN",
    "output": "deals-in.json",
    "affiliate_tag": "syncflowin-21",
    "amazon_domain": "amazon.in",
    "currency_symbol": "₹",
    "price_regex": r"(?:₹|Rs\.?|INR|@\s*(?:Rs\.?)?\s*)\s?([0-9,]+(?:\.[0-9]{1,2})?)",
    "price_format": lambda p: f"₹{p}",
    "feeds": [
        # Reddit India deal communities
        "https://www.reddit.com/r/indiadeals/.rss",
        "https://www.reddit.com/r/dealsforindia/.rss",
        "https://www.reddit.com/r/IndianGaming/.rss",
        "https://www.reddit.com/r/AmazonIndia/.rss",
        "https://www.reddit.com/r/IndianTech/.rss",
    ],
    "deal_blog_domains": [],
    # Price thresholds for scoring (in INR — roughly 80x USD)
    "price_thresholds": {"high_score": 800, "med_score": 2000, "low_score": 4000},
}

# ---- REGEX & HELPERS ----
ASIN_REGEX = r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})"
AMZN_SHORT_REGEX = r'(https?://(?:amzn\.to|amzn\.com)/[^\s"<>\']+)'
DISCOUNT_REGEX = r"(\d+)%"


def make_amazon_link_regex(domain):
    """Build regex to match Amazon URLs for the given domain."""
    escaped = domain.replace(".", r"\.")
    return rf'(https?://(?:www\.)?{escaped}/[^\s"<>\']+)'


def amazon_image_from_asin(asin):
    """Fallback: construct Amazon product page URL to scrape image later."""
    if not asin:
        return None
    return None  # Return None to force fetching from page


def fetch_amazon_image(asin, domain="amazon.com"):
    """Fetch the actual product image from Amazon product page."""
    if not asin:
        return None

    try:
        url = f"https://www.{domain}/dp/{asin}"
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
                    if img_url and img_url.startswith('http') and ('amazon' in img_url or 'media-amazon.com' in img_url):
                        return img_url

        # Try Open Graph image as fallback
        og_image = soup.select_one('meta[property="og:image"]')
        if og_image and og_image.get('content'):
            return og_image['content']

        return None
    except Exception as e:
        print(f"[IMAGE] Error fetching image for {asin}: {e}")
        return None


def add_affiliate_tag(url, region):
    """Adds our affiliate tag to a URL, preserving existing query params."""
    domain = region["amazon_domain"]
    tag = region["affiliate_tag"]
    if not url or domain not in url:
        return url
    # Clean URL and add affiliate tag
    base_url = url.split('?')[0] if '?' in url else url
    parsed = urlparse(base_url)
    q = {"tag": tag}
    return urlunparse(parsed._replace(query=urlencode(q)))


def resolve_short_url(short_url, domain="amazon.com"):
    """Resolve amzn.to or other short URLs to full Amazon URL."""
    try:
        response = requests.head(short_url, headers=HEADERS, timeout=5, allow_redirects=True)
        final_url = response.url
        if domain in final_url:
            return final_url
    except:
        pass
    return None


def extract_amazon_url(text, fallback="", region=None):
    """Finds an Amazon URL in the text or uses fallback."""
    domain = region["amazon_domain"] if region else "amazon.com"
    link_regex = make_amazon_link_regex(domain)

    # Check fallback first
    if fallback:
        if domain in fallback:
            return fallback
        if "amzn.to" in fallback or "amzn.com" in fallback:
            resolved = resolve_short_url(fallback, domain)
            if resolved:
                return resolved

    if not text:
        return None

    # First try to find direct Amazon URLs
    matches = re.findall(link_regex, text)
    for match in matches:
        if domain in match and extract_asin(match):
            return match

    # Then try amzn.to short links
    short_matches = re.findall(AMZN_SHORT_REGEX, text)
    for short_url in short_matches:
        if DEBUG:
            print(f"[DEBUG] Resolving short URL: {short_url}")
        resolved = resolve_short_url(short_url, domain)
        if resolved and extract_asin(resolved):
            return resolved

    return matches[0] if matches else None


def extract_asin(url):
    """Extracts the 10-digit ASIN from an Amazon URL."""
    if not url:
        return None
    m = re.search(ASIN_REGEX, url)
    return m.group(1) if m else None


def extract_price_from_title(title, region):
    """Fallback to extract a price from the title string."""
    if not title:
        return None
    price_regex = region["price_regex"]
    price_format = region["price_format"]
    m = re.search(price_regex, title)
    if m:
        return price_format(m.group(1).replace(',', ''))

    # Also try $ for India RSS feeds that sometimes use USD
    if region["name"] == "IN":
        m = re.search(r"\$([0-9,]+(?:\.[0-9]{1,2})?)", title)
        if m:
            return f"${m.group(1).replace(',', '')}"
        # Try bare number with /- suffix (e.g. "3,020/-")
        m = re.search(r"([0-9,]+)\s*/\s*-", title)
        if m:
            return region["price_format"](m.group(1).replace(',', ''))
    return None


def extract_discount_from_title(title):
    """Extract discount percentage from title."""
    if not title:
        return 0
    m = re.search(DISCOUNT_REGEX, title)
    return int(m.group(1)) if m else 0


def extract_image_from_summary(summary, domain="amazon.com"):
    """Extracts the best image URL from HTML summary content."""
    if not summary:
        return None
    soup = BeautifulSoup(summary, "html.parser")

    # Look for Amazon images first (they're higher quality)
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if src and ("amazon" in src or "media-amazon.com" in src):
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
    # Remove trailing price info (USD)
    title = re.sub(r'-\s*\$\d+(\.\d{2})?', '', title)
    # Remove trailing price info (INR)
    title = re.sub(r'-\s*₹[\d,]+', '', title)
    title = re.sub(r'-\s*Rs\.?\s*[\d,]+', '', title)
    # Remove common deal site prefixes
    title = re.sub(r'^(Deal:|Hot Deal:|Amazon Deal:)', '', title, flags=re.IGNORECASE)
    # Collapse whitespace
    title = re.sub(r'\s+', ' ', title).strip()
    return title


def calculate_deal_score(title, price, discount, region):
    """Calculate a quality score for the deal."""
    score = 0
    thresholds = region["price_thresholds"]
    currency = region["currency_symbol"]

    # Price attractiveness (lower price = higher score)
    if price:
        try:
            price_val = float(price.replace(currency, '').replace('$', '').replace('₹', '').replace(',', '').strip())
            if price_val <= thresholds["high_score"]:
                score += 3
            elif price_val <= thresholds["med_score"]:
                score += 2
            elif price_val <= thresholds["low_score"]:
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
    if any(word in title_lower for word in ['new', 'hot', 'limited', 'exclusive', 'lowest', 'best']):
        score += 1
    return score


def is_duplicate_title(title1, title2, threshold=0.85):
    """Check if two titles are duplicates using similarity matching."""
    return SequenceMatcher(None, title1.lower(), title2.lower()).ratio() >= threshold


def scrape_amazon_details(asin, title_price_fallback, region):
    """Return fallback data for speed."""
    default_price = f"{region['currency_symbol']}?" if not title_price_fallback else title_price_fallback
    return {
        "price": default_price,
        "rating": "N/A",
        "reviews": "N/A"
    }


def categorize(title):
    """Enhanced categorization with more categories and better matching."""
    t = title.lower()

    if any(x in t for x in ["gaming", "nintendo", "playstation", "xbox", "steam", "game", "controller", "joystick"]):
        return "Gaming"
    if any(x in t for x in ["ssd", "hdd", "nvme", "gpu", "graphics card", "ram", "memory", "keyboard", "mouse", "monitor", "router", "ipad", "iphone", "tablet", "laptop", "pc case", "cable", "charger", "headphones", "earbuds", "usb", "computer", "wifi", "powerbank", "power bank"]):
        return "Tech"
    if any(x in t for x in ["sofa", "mattress", "vacuum", "kitchen", "cookware", "air purifier", "heater", "humidifier", "purifier", "bulb", "lamp", "blanket", "towel", "appliance", "mixer", "grinder", "induction"]):
        return "Home"
    if any(x in t for x in ["shoe", "fitness", "yoga", "treadmill", "dumbbell", "protein", "sneakers", "running", "workout", "gym", "exercise", "bike"]):
        return "Fitness"
    if any(x in t for x in ["case", "charger", "backpack", "watch band", "wallet", "sleeve", "stand", "mount", "holder", "cover", "tempered glass", "screen protector"]):
        return "Accessories"
    if any(x in t for x in ["gift", "holiday", "christmas", "diwali", "present", "lego", "toy", "puzzle", "board game", "card game", "rakhi"]):
        return "Gifts"
    if any(x in t for x in ["baby", "kids", "children", "diaper", "stroller", "crib", "educational"]):
        return "Baby"
    if any(x in t for x in ["shampoo", "conditioner", "soap", "lotion", "perfume", "makeup", "skincare", "hair", "nail", "cream", "face wash"]):
        return "Beauty"
    if any(x in t for x in ["dog", "cat", "pet", "puppy", "kitten", "collar", "leash"]):
        return "Pets"

    return "General"


def fetch_amazon_url_from_page(page_url, region):
    """Try to fetch Amazon URL and image from a deal page (for Slickdeals etc).
    Returns tuple: (amazon_url, image_url)
    """
    domain = region["amazon_domain"]
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
            if domain in href:
                if '/dp/' in href or '/gp/' in href or '/product/' in href:
                    if extract_asin(href):
                        amazon_url = href
                        break
            # Also check for amzn.to short links
            elif 'amzn.to' in href or 'amzn.com' in href:
                try:
                    redirect_resp = requests.head(href, headers=HEADERS, timeout=5, allow_redirects=True)
                    final_url = redirect_resp.url
                    if domain in final_url and extract_asin(final_url):
                        amazon_url = final_url
                        break
                except:
                    pass

        # Fallback: look via regex in full page content
        if not amazon_url or not extract_asin(amazon_url):
            amazon_url = extract_amazon_url(response.text, region=region)

        # Extract image from the page
        image_url = extract_image_from_summary(str(soup), domain)

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


def run_for_region(region, feed_cache):
    """Main deal-fetching function for a single region."""
    region_name = region["name"]
    domain = region["amazon_domain"]
    output = region["output"]
    feeds = region["feeds"]
    deal_blog_domains = region["deal_blog_domains"]

    print(f"\n{'='*60}")
    print(f"  FETCHING DEALS FOR: {region_name} ({domain})")
    print(f"  Affiliate tag: {region['affiliate_tag']}")
    print(f"{'='*60}")

    deals = []
    seen_asins = set()
    seen_titles = set()
    category_counts = defaultdict(int)
    max_per_category = 25

    for feed_url in feeds:
        if len(deals) >= MAX_DEALS:
            print(f"[{region_name}] Max deals reached, stopping early.")
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
            amazon_url = extract_amazon_url(all_content, fallback_link, region)
            page_image = None  # Image found from deal page

            # If no direct Amazon URL, try fetching from the page for known deal sites
            if not amazon_url:
                combined = f"{title} {all_content}".lower()
                is_deal_blog = any(d in fallback_link for d in deal_blog_domains)

                # Check for Amazon-related keywords
                amazon_keywords = ["amazon", "amzn", "prime deal", "shipped", "subscribe & save",
                                   "great indian", "lightning deal", "deal of the day"]
                has_amazon_keyword = any(kw in combined for kw in amazon_keywords)

                # For deal blogs, always try to fetch the page
                if is_deal_blog and (has_amazon_keyword or "deal" in title.lower()):
                    if DEBUG:
                        print(f"[DEBUG] Fetching page: {fallback_link[:60]}...")
                    amazon_url, page_image = fetch_amazon_url_from_page(fallback_link, region)

            if not amazon_url:
                if DEBUG and "amazon" in f"{title} {all_content}".lower():
                    print(f"[DEBUG] [{region_name}] No Amazon URL found for: {title[:50]}...")
                continue

            # Ensure URL matches this region's domain
            if domain not in amazon_url:
                if DEBUG:
                    print(f"[DEBUG] [{region_name}] URL not for {domain}: {amazon_url[:60]}...")
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
            image = extract_image_from_summary(all_content, domain)

            # 2. Try image from deal page (if we fetched it)
            if not image and page_image:
                image = page_image

            # 3. Fetch directly from Amazon product page
            if not image:
                print(f"[IMAGE] Fetching image from Amazon for {asin}...")
                image = fetch_amazon_image(asin, domain)

            # If no image found, use a placeholder (don't skip the deal)
            if not image:
                if DEBUG:
                    print(f"[DEBUG] No image for: {cleaned_title[:40]}... using placeholder")
                image = f"https://m.media-amazon.com/images/I/{asin}._AC_SL500_.jpg"

            # Extract discount and price
            discount = extract_discount_from_title(title)
            title_price_fallback = extract_price_from_title(title, region)
            details = scrape_amazon_details(asin, title_price_fallback, region)

            # Calculate deal quality score
            score = calculate_deal_score(cleaned_title, details["price"], discount, region)

            # Build final URL with affiliate tag
            final_url = add_affiliate_tag(amazon_url, region)

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

            print(f"[DEAL] [{region_name}] Added: {cleaned_title[:50]}... ({details['price']}) - {category}")

    # Sort by quality score and recency
    deals.sort(key=lambda x: (x['score'], x['timestamp']), reverse=True)

    print(f"\n[{region_name}] Found {len(deals)} valid Amazon deals.")
    print(f"[{region_name}] CATEGORY BREAKDOWN:")
    for cat, count in sorted(category_counts.items()):
        print(f"  {cat}: {count} deals")

    # Write to file
    with open(output, "w") as f:
        json.dump({"deals": deals, "updated": int(time.time())}, f, indent=2)

    print(f"[{region_name}] Saved {len(deals)} deals to {output}")
    return len(deals)


def main():
    """Fetch deals for all regions sequentially."""
    feed_cache = load_feed_cache()

    # Run US deals
    us_count = run_for_region(REGION_US, feed_cache)

    # Run India deals
    in_count = run_for_region(REGION_IN, feed_cache)

    # Save feed cache (shared across regions)
    save_feed_cache(feed_cache)

    print(f"\n{'='*60}")
    print(f"  ALL DONE: US={us_count} deals, IN={in_count} deals")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
