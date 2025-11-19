import requests
import json
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

API_HOST = "realtime-amazon-data.p.rapidapi.com"
API_KEY = "REPLACE_WITH_SECRET_ENV"   # we will use GitHub secret
AFFILIATE_TAG = "syncflow-20"
OUTPUT_FILE = "deals.json"
MAX_ITEMS = 100


def add_affiliate(url):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["tag"] = AFFILIATE_TAG
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def upscale(img):
    if not img:
        return None
    return f"https://wsrv.nl/?url={img}&w=600&h=600&fit=cover"


def fetch_page(page):
    url = f"https://{API_HOST}/deals?country=US&page={page}"
    headers = {
        "x-rapidapi-key": API_KEY,
        "x-rapidapi-host": API_HOST
    }

    print(f"[DEALS] Fetching page {page} ...")
    r = requests.get(url, headers=headers, timeout=20)

    if r.status_code != 200:
        print("ERROR:", r.text)
        return []

    data = r.json()
    if "data" not in data:
        print("Invalid response:", data)
        return []

    return data["data"].get("deals", [])


def main():
    all_deals = []
    page = 1

    while len(all_deals) < MAX_ITEMS:
        deals = fetch_page(page)
        if not deals:
            break

        for d in deals:
            product = {
                "title": d.get("title"),
                "image": upscale(d.get("thumbnail")),
                "price": d.get("price") or "$?",
                "url": add_affiliate(d.get("product_url")),
                "category": d.get("category") or "Deals"
            }
            all_deals.append(product)

            if len(all_deals) >= MAX_ITEMS:
                break

        page += 1

    print(f"[DEALS] Total collected: {len(all_deals)}")

    output = {"deals": all_deals}
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print("[DEALS] Saved deals.json ✔")


if __name__ == "__main__":
    main()
