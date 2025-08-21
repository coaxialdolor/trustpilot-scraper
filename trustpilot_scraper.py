import csv
import json
import time
import os
import glob
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

# ----------------------- Helpers -----------------------

def make_filename(prefix, mode, pages=None, months=None, keywords=None):
    parts = [prefix]
    if mode == 1 and pages:
        parts.append(f"pages-{pages}")
    if mode == 2 and months:
        parts.append(f"months-{months}")
    if mode == 3 and keywords:
        # Join keywords with ',_' exactly once, keep multi-word keywords intact
        clean_keys = ",_".join([k.strip().replace(" ", "_") for k in keywords])
        parts.append(f"keywords-{clean_keys}")
    parts.append(datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
    return "_".join(parts)

def save_reviews(reviews, filename_base):
    csv_file = f"{filename_base}.csv"
    json_file = f"{filename_base}.json"
    html_file = f"{filename_base}.html"

    # CSV
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["reviewer", "date", "link", "text"])
        writer.writeheader()
        writer.writerows(reviews)

    # JSON
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({"reviews": reviews}, f, indent=2, ensure_ascii=False)

    # HTML
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(f"""
        <html>
        <head>
            <meta charset='utf-8'>
            <title>Trustpilot Reviews</title>
            <style>
                body {{ font-family: Arial, sans-serif; background: #f2f2f2; padding: 20px; }}
                h1 {{ text-align: center; color: #333; }}
                .card {{ background: #fff; border: 1px solid #ccc; border-radius: 8px; 
                        padding: 15px; margin: 15px auto; max-width: 800px; 
                        box-shadow: 0 2px 5px rgba(0,0,0,0.1); transition: transform 0.2s; }}
                .card:hover {{ transform: scale(1.02); }}
                .reviewer {{ font-weight: bold; color: #222; }}
                .date {{ color: #555; font-size: 0.9em; margin-bottom: 5px; }}
                .link {{ color: #1a0dab; text-decoration: none; font-size: 0.9em; }}
                .text {{ margin-top: 10px; line-height: 1.5; color: #333; white-space: pre-wrap; }}
                .summary {{ max-width: 800px; margin: 20px auto; background: #eee; padding: 15px; border-radius: 8px; }}
            </style>
        </head>
        <body>
            <h1>Trustpilot Reviews</h1>
            <div class="summary">
                <strong>Total reviews scraped:</strong> {len(reviews)}<br>
                <strong>Date/time of scrape:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}<br>
            </div>
        """)
        for r in reviews:
            f.write(f"""
            <div class='card'>
                <div class='reviewer'>{r['reviewer']}</div>
                <div class='date'>{r['date']}</div>
                <div class='link'><a href='{r['link']}' target='_blank'>{r['link']}</a></div>
                <div class='text'>{r['text']}</div>
            </div>
            """)
        f.write("</body></html>")

    print(f"\n💾 Saved {len(reviews)} reviews to {csv_file}, {json_file}, {html_file}")

def load_previous_reviews(filename=None):
    if filename and Path(filename).exists():
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
            reviews = data.get("reviews", [])
            print(f"🔄 Loaded {len(reviews)} reviews from {filename}")
            return reviews
    return []

def extract_reviewer_name(block):
    try:
        name = block.find_element(By.CSS_SELECTOR, "span[data-consumer-name-typography]").text.strip()
        if name:
            return name
    except NoSuchElementException:
        pass
    return "Unknown"

def extract_review_permalink(block):
    try:
        a = block.find_element(By.CSS_SELECTOR, "a[href*='/reviews/']")
        return a.get_attribute("href")
    except NoSuchElementException:
        return None

def maybe_click_see_more(driver, block):
    try:
        btn = block.find_element(By.CSS_SELECTOR, "button[data-service-review-toggle-text-show]")
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.5)
    except NoSuchElementException:
        pass

def extract_review_text(block):
    ps = block.find_elements(By.CSS_SELECTOR, "p")
    texts = [p.text.strip() for p in ps if p.text.strip()]
    return max(texts, key=len) if texts else ""

# ----------------------- Scraper -----------------------

def scrape_reviews(base_url, mode, max_pages=None, months=None, keywords=None, resume=False, headless=True):
    reviews = []
    seen_links = set()
    cutoff_date = datetime.now() - timedelta(days=30 * months) if months else None

    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    max_retries = 3
    page = 1
    interrupted = False

    filename_base = make_filename("europcar_reviews", mode, max_pages, months, keywords)
    resume_file = None
    if resume:
        json_files = glob.glob("europcar_reviews_*.json")
        if json_files:
            latest_file = max(json_files, key=os.path.getctime)
            reviews = load_previous_reviews(latest_file)
            seen_links = {r["link"] for r in reviews}
            print(f"🔄 Resuming from {latest_file}")

    # Prepare keyword filters
    keyword_and = []
    keyword_or = []
    if keywords:
        for k in keywords:
            k = k.strip()
            if k.startswith("+") or "AND" in k.upper():
                keyword_and.append(k.replace("+","").replace("AND","").strip())
            else:
                keyword_or.append(k)

    try:
        while True:
            if mode == 1 and max_pages and page > max_pages:
                print(f"⏹ Reached page limit {max_pages}. Stopping.")
                break

            url = f"{base_url}&sort=recency&page={page}"

            for attempt in range(1, max_retries+1):
                try:
                    print(f"\n🌍 Page {page}: {url} (Attempt {attempt})")
                    driver.get(url)
                    time.sleep(2)
                    break
                except WebDriverException as e:
                    print(f"⚠️ Failed to load page {page} (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        print("❌ Max retries reached, skipping this page.")
                        url = None
                    else:
                        time.sleep(5)
            if not url:
                page += 1
                continue

            blocks = driver.find_elements(By.CSS_SELECTOR, "article")
            if not blocks:
                print("⚠️ No review cards found. Continuing to next page.")
                page += 1
                time.sleep(1.2)
                continue

            page_found = 0
            page_added = 0

            for i, block in enumerate(blocks):
                page_found += 1
                maybe_click_see_more(driver, block)
                if i == 0:
                    time.sleep(0.5)

                reviewer = extract_reviewer_name(block)
                text = extract_review_text(block)
                review_link = extract_review_permalink(block) or url

                try:
                    date_el = block.find_element(By.TAG_NAME, "time")
                    date_str = date_el.get_attribute("datetime").split("T")[0]
                    review_date = datetime.strptime(date_str, "%Y-%m-%d")
                except Exception:
                    continue

                if cutoff_date and review_date < cutoff_date:
                    continue

                if review_link in seen_links:
                    continue
                seen_links.add(review_link)

                # Keyword filter logic (AND + OR)
                add_review = True
                if keywords:
                    if keyword_and and not all(k.lower() in text.lower() for k in keyword_and):
                        add_review = False
                    if keyword_or and not any(k.lower() in text.lower() for k in keyword_or):
                        add_review = False if not keyword_and else add_review

                if not add_review:
                    continue

                reviews.append({
                    "reviewer": reviewer,
                    "date": review_date.strftime("%Y-%m-%d"),
                    "link": review_link,
                    "text": text
                })
                page_added += 1
                print(f"✅ Added review ({review_date.strftime('%Y-%m-%d')}): {text[:60]}...")

            print(f"📄 Page {page}: found {page_found} cards, added {page_added} reviews (total: {len(reviews)})")

            save_reviews(reviews, filename_base)
            page += 1
            time.sleep(1.2)

    except KeyboardInterrupt:
        interrupted = True
        print("\n🛑 Interrupted by user.")

    finally:
        driver.quit()
        if interrupted:
            print(f"ℹ️ Collected {len(reviews)} reviews before interrupt.")
        save_reviews(reviews, filename_base)

    return reviews

# ----------------------- Main -----------------------

if __name__ == "__main__":
    BASE_URL = "https://www.trustpilot.com/review/www.europcar.co.uk?stars=1"

    resume_choice = input("Resume previous scan if available? (y/n): ").strip().lower()
    resume = resume_choice == "y"

    print("Choose scraping mode:")
    print("1. Pages")
    print("2. Months back")
    print("3. Keywords")
    mode_in = input("Enter 1,2,3: ").strip()

    if mode_in == "1":
        pages_in = input("How many pages to scrape? (number or 'all'): ").strip().lower()
        max_pages = None if pages_in=="all" else int(pages_in)
        scrape_reviews(BASE_URL, mode=1, max_pages=max_pages, resume=resume)
    elif mode_in == "2":
        months_in = int(input("How many months back? ").strip())
        scrape_reviews(BASE_URL, mode=2, months=months_in, resume=resume)
    elif mode_in == "3":
        raw = input("Enter keywords (',' = OR, '+' or 'AND' = AND): ").strip()
        keywords = [w.strip() for w in raw.split(",") if w.strip()]
        scrape_reviews(BASE_URL, mode=3, keywords=keywords, resume=resume)
    else:
        print("❌ Invalid choice. Exiting.")