import requests
import json
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

API_URL = "https://realtime-amazon-data.p.rapidapi.com/best-sellers"
AFFILIATE_TAG = "syncflow-20"
OUTPUT_FILE = "deals.json"

# 4 categories: 25 each = 100 items
CATEGORIES = [
    ("electronics", 25),
    ("kitchen", 25),
    ("home", 25),
    ("mobile-apps", 25),
]

HEADERS = {
    "x-rapidapi-host": "realtime-amazon-data.p.rapidapi.com",
    "x-rapidapi-key": "${{ secrets.RAPIDAPI_KEY }}",   # GitHub injects key
}

def add_affiliate(url):
    if "tag=" in url:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["tag"] = AFFILIATE_TAG
    new_q = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_q))

def upscale_image(img_url):
    if not img_url:
        return img_url
    return f"https://wsrv.nl/?url={img_url}&w=600&h=400&fit=cover"

def fetch_best_sellers(category, limit):
    print(f"[DEALS] Fetching category: {category}")

    params = {
        "category": category,
        "country": "us",
        "page": "1"
    }

    resp = requests.get(API_URL, headers=HEADERS, params=params, timeout=20)
    
    if resp.status_code != 200:
        print("[ERROR] API failure:", resp.text)
        return []

    data = resp.json()
    items = data.get("data", [])
    deals = []

    for item in items[:limit]:
        deals.append({
            "title": item.get("product_title", "Untitled"),
            "image": upscale_image(item.get("product_photo")),
            "price": item.get("product_price", "N/A"),
            "url": add_affiliate(item.get("product_url", "")),
            "category": category.capitalize()
        })

    print(f"[DEALS] Fetched {len(deals)} from {category}")
    return deals

def main():
    all_deals = []

    for category, limit in CATEGORIES:
        all_deals.extend(fetch_best_sellers(category, limit))

    print(f"[DEALS] Total collected: {len(all_deals)}")

    output = {"deals": all_deals}

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print("[DEALS] Saved deals.json")

if __name__ == "__main__":
    main()
