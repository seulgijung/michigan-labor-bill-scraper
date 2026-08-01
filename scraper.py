# Michigan labor-bill scraper: initial full scrape + periodic hash-based update
# Run this file to perform an update run (used by GitHub Actions).

import os
import re
import json
import time
import random
import hashlib
import requests
from datetime import datetime
from urllib.parse import urljoin, urlencode, quote
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# --- Configuration ---------------------------------------------------------

# Load API key from .env locally; on GitHub Actions it comes from Secrets (same env var)
load_dotenv()
SCRAPEOPS_API_KEY = os.getenv("SCRAPEOPS_API_KEY")

BASE = "https://legislature.mi.gov"

# Labor topic = union of these three categories
LABOR_CATEGORIES = ["Labor", "Employment security", "Worker's compensation"]

# Browser-like headers reused by the session
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

session = requests.Session()
session.headers.update(headers)

# --- Networking ------------------------------------------------------------

def fetch(url, min_delay=1.0, max_delay=2.0, retries=3, backoff=10.0):
    # Route every request through the ScrapeOps proxy to avoid IP bot-blocking
    time.sleep(random.uniform(min_delay, max_delay))
    proxy_endpoint = "https://proxy.scrapeops.io/v1/"
    params = {"api_key": SCRAPEOPS_API_KEY, "url": url}
    last_err = None
    for attempt in range(retries):
        try:
            resp = session.get(proxy_endpoint, params=params, timeout=120)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_err = e
            time.sleep(backoff * (attempt + 1))
    raise last_err

# --- JSON persistence ------------------------------------------------------

def load_json(path, default):
    # Read an existing JSON file, or return a default if it doesn't exist yet
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json(path, obj):
    # Write an object to a JSON file (used as a checkpoint after each bill)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

# --- Bill-id collection ----------------------------------------------------

def collect_bill_ids():
    # Union of bill_ids across the three labor-related categories
    all_ids = set()
    for cat in LABOR_CATEGORIES:
        url = f"{BASE}/Search/ExecuteSearch?sessions=2025-2026&docTypes=Bills&category={quote(cat)}"
        r = fetch(url)
        ids = set(re.findall(r"objectName=(20\d\d-(?:SB|HB)-\d+)", r.text))
        all_ids |= ids
    return sorted(all_ids)

# --- Single-bill parsing ---------------------------------------------------

def scrape_bill(bill_id):
    # Fetch + parse one bill into a single record dictionary
    detail_url = f"{BASE}/Bills/Bill?ObjectName={bill_id}"
    resp = fetch(detail_url)
    soup = BeautifulSoup(resp.text, "html.parser")

    chamber = "Senate" if "-SB-" in bill_id else "House"

    heading_el = soup.find(id="BillHeading")
    title = heading_el.get_text(strip=True) if heading_el else None

    subject_el = soup.find(id="ObjectSubject")
    subject = subject_el.get_text(strip=True) if subject_el else None

    sponsor_el = soup.find("a", class_="primarySponsor")
    primary_sponsor = sponsor_el.get_text(strip=True) if sponsor_el else None

    sponsor_links = soup.select("#SponsorList li a")
    cosponsors = [
        a.get_text(strip=True)
        for a in sponsor_links
        if "primarySponsor" not in (a.get("class") or [])
    ]

    actions = []
    for row in soup.select("#History table tbody tr"):
        cells = row.find_all("td")
        if len(cells) >= 3:
            actions.append({
                "date": cells[0].get_text(strip=True),
                "description": cells[2].get_text(strip=True),
            })

    text_url = None
    doc_rows = soup.select(".billDocuments .billDocRow")
    for r in doc_rows:
        label_el = r.select_one(".text")
        label = label_el.get_text(strip=True) if label_el else ""
        link = r.select_one(".html a")
        if link and "Introduced Bill" in label:
            text_url = urljoin(BASE, link["href"])
            break
    if text_url is None and doc_rows:
        first_link = doc_rows[0].select_one(".html a")
        if first_link:
            text_url = urljoin(BASE, first_link["href"])

    full_text = None
    if text_url:
        tr = fetch(text_url)
        full_text = BeautifulSoup(tr.text, "html.parser").get_text(separator="\n", strip=True)

    actions_joined = "\n".join(f"{a['date']} {a['description']}" for a in actions)
    content_hash = hashlib.md5(actions_joined.encode()).hexdigest()

    return {
        "bill_id": bill_id,
        "chamber": chamber,
        "url": detail_url,
        "content_hash": content_hash,
        "last_scraped": datetime.now().isoformat(timespec="seconds"),
        "title": title,
        "subject": subject,
        "primary_sponsor": primary_sponsor,
        "cosponsors": cosponsors,
        "actions": actions,
        "text_url": text_url,
        "full_text": full_text,
    }

def get_content_hash(bill_id):
    # Fetch only the detail page and compute the actions-based hash (no full text)
    detail_url = f"{BASE}/Bills/Bill?ObjectName={bill_id}"
    resp = fetch(detail_url)
    soup = BeautifulSoup(resp.text, "html.parser")

    actions = []
    for row in soup.select("#History table tbody tr"):
        cells = row.find_all("td")
        if len(cells) >= 3:
            actions.append({
                "date": cells[0].get_text(strip=True),
                "description": cells[2].get_text(strip=True),
            })

    actions_joined = "\n".join(f"{a['date']} {a['description']}" for a in actions)
    return hashlib.md5(actions_joined.encode()).hexdigest()

# --- Scrape runs -----------------------------------------------------------

def scrape_all(bill_ids):
    # Initial/full scrape with resume (skip saved) + checkpoint (save each)
    data = load_json("data.json", {})
    error_log = load_json("error_log.json", [])
    for i, bill_id in enumerate(bill_ids, start=1):
        if bill_id in data:
            print(f"[{i}/{len(bill_ids)}] {bill_id} (skip)")
            continue
        try:
            data[bill_id] = scrape_bill(bill_id)
            save_json("data.json", data)
            print(f"[{i}/{len(bill_ids)}] {bill_id} (saved)")
        except Exception as e:
            error_log.append({"bill_id": bill_id, "error": str(e)})
            save_json("error_log.json", error_log)
            print(f"[{i}/{len(bill_ids)}] {bill_id} (ERROR)")
    return data, error_log

def update_scrape():
    # Periodic update: detect new (added) and changed (modified) labor bills via hash
    data = load_json("data.json", {})
    error_log = []
    change_log = []
    run_ts = datetime.now().isoformat(timespec="seconds")

    current_ids = collect_bill_ids()

    for i, bill_id in enumerate(current_ids, start=1):
        try:
            if bill_id not in data:
                data[bill_id] = scrape_bill(bill_id)
                change_log.append({"bill_id": bill_id, "change": "added", "run": run_ts})
                save_json("data.json", data)
                print(f"[{i}/{len(current_ids)}] {bill_id} (added)")
            else:
                new_hash = get_content_hash(bill_id)
                if new_hash != data[bill_id]["content_hash"]:
                    data[bill_id] = scrape_bill(bill_id)
                    change_log.append({"bill_id": bill_id, "change": "modified", "run": run_ts})
                    save_json("data.json", data)
                    print(f"[{i}/{len(current_ids)}] {bill_id} (modified)")
                else:
                    print(f"[{i}/{len(current_ids)}] {bill_id} (skip)")
        except Exception as e:
            error_log.append({"bill_id": bill_id, "error": str(e), "run": run_ts})
            save_json("error_log.json", error_log)
            print(f"[{i}/{len(current_ids)}] {bill_id} (ERROR)")

    save_json("change_log.json", change_log)
    return data, change_log, error_log

# --- Entry point -----------------------------------------------------------

if __name__ == "__main__":
    # Running this file performs an update run (what GitHub Actions executes)
    update_scrape()