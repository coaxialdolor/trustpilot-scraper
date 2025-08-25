import csv
import json
import time
import os
import glob
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

# ----------------------- Helpers -----------------------

def make_filename(prefix, mode, pages=None, months=None, keywords=None, start_date=None, end_date=None):
    parts = [prefix]
    if mode == 1 and pages:
        parts.append(f"pages-{pages}")
    if mode == 2 and months:
        parts.append(f"months-{months}")
    if keywords:
        # Join keywords with ',_' exactly once, keep multi-word keywords intact
        clean_keys = ",_".join([k.strip().replace(" ", "_") for k in keywords])
        parts.append(f"keywords-{clean_keys}")
    if mode == 4 and start_date and end_date:
        parts.append(f"dates-{start_date}_to_{end_date}")
    parts.append(datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
    return "_".join(parts)


def canonical_link(href: str | None) -> str:
    if not href:
        return ""
    # Remove query params and anchors for stable identity
    base = href.split("#")[0]
    base = base.split("?")[0]
    return base.strip()


def stable_review_key(reviewer: str, date_str: str, link: str, text: str) -> str:
    """Create a robust identity for a review.
    Prefer the canonical permalink when available; otherwise hash core fields.
    """
    link_key = canonical_link(link)
    if link_key:
        return f"link::{link_key}"
    digest = hashlib.sha256(
        (reviewer.strip() + "|" + date_str.strip() + "|" + text.strip()).encode("utf-8")
    ).hexdigest()
    return f"hash::{digest}"


def save_reviews(reviews, filename_base, mode=None, max_pages=None, months=None, keywords=None, start_date=None, end_date=None):
    out_dir = Path(filename_base)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_file = str(out_dir / "results.csv")
    json_file = str(out_dir / "results.json")
    html_file = str(out_dir / "results.html")

    # CSV
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["reviewer", "date", "link", "text"])
        writer.writeheader()
        writer.writerows(reviews)

    # JSON
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({"reviews": reviews}, f, indent=2, ensure_ascii=False)

    # Build search criteria text
    criteria_text = ""
    if mode == 1 and max_pages:
        criteria_text = f"<strong>Search criteria:</strong> Limited to {max_pages} pages<br>"
    elif mode == 1 and not max_pages:
        criteria_text = f"<strong>Search criteria:</strong> All pages<br>"
    elif mode == 2 and months:
        criteria_text = f"<strong>Search criteria:</strong> Reviews from the last {months} months<br>"
    elif mode == 3 and keywords:
        keyword_display = []
        for k in keywords:
            if k.startswith("+"):
                keyword_display.append(f"+{k[1:]} (AND)")
            elif "AND" in k.upper():
                clean_k = k.upper().replace("AND", "").strip()
                keyword_display.append(f"{clean_k} (AND)")
            else:
                keyword_display.append(f"{k} (OR)")
        criteria_text = f"<strong>Search criteria:</strong> Keywords - {', '.join(keyword_display)}<br>"
    elif mode == 4 and start_date and end_date:
        criteria_text = f"<strong>Search criteria:</strong> Date interval from {start_date} to {end_date}<br>"

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
                {criteria_text}
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

    print(f"\n💾 Saved {len(reviews)} reviews to folder: {out_dir}")


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
    # Prefer the main review paragraph; ignore 'See more' teaser content
    try:
        main = block.find_element(By.CSS_SELECTOR, "div[data-service-review-text-typography] p")
        return main.text.strip()
    except NoSuchElementException:
        ps = block.find_elements(By.CSS_SELECTOR, "p")
        texts = [p.text.strip() for p in ps if p.text.strip() and not p.text.strip().endswith("See more")]
        return max(texts, key=len) if texts else ""


# ----------------------- Scraper -----------------------

def scrape_reviews(base_url, mode, max_pages=None, months=None, keywords=None, resume=False, headless=True, start_date=None, end_date=None, resume_file=None):
    reviews = []
    seen_keys = set()
    cutoff_date = datetime.now() - timedelta(days=30 * months) if months else None
    start_dt = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") if end_date else None

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

    filename_base = make_filename("europcar_reviews", mode, max_pages, months, keywords, start_date, end_date)
    if resume:
        source_json = None
        if resume_file and Path(resume_file).exists():
            source_json = str(resume_file)
        else:
            json_files = glob.glob("europcar_reviews_*/results.json") + glob.glob("europcar_reviews_*.json")
            if json_files:
                source_json = max(json_files, key=os.path.getctime)
        if source_json:
            reviews = load_previous_reviews(source_json)
            for r in reviews:
                key = stable_review_key(r.get("reviewer", ""), r.get("date", ""), r.get("link", ""), r.get("text", ""))
                seen_keys.add(key)
            print(f"🔄 Resuming from {source_json}")

    # Prepare keyword filters
    keyword_and = []
    keyword_or = []
    if keywords:
        for k in keywords:
            k = k.strip()
            if k.startswith("+") or "AND" in k.upper():
                keyword_and.append(k.replace("+", "").replace("AND", "").strip())
            else:
                keyword_or.append(k)

    # Termination safety
    consecutive_no_additions = 0
    max_consecutive_no_additions = 3  # conservative to avoid stopping too early
    previous_page_signature = None
    same_page_signature_count = 0

    try:
        while True:
            if mode == 1 and max_pages and page > max_pages:
                print(f"⏹ Reached page limit {max_pages}. Stopping.")
                break

            url = f"{base_url}&sort=recency&page={page}"

            for attempt in range(1, max_retries + 1):
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
                # If we keep seeing no blocks, count as no-additions
                consecutive_no_additions += 1
                if (mode != 1 or not max_pages) and consecutive_no_additions >= max_consecutive_no_additions:
                    print("⏹ No content for several consecutive pages. Stopping.")
                    break
                continue

            page_found = 0
            page_added = 0
            page_links_for_signature = []
            page_all_older_than_cutoff = True if cutoff_date else False

            for i, block in enumerate(blocks):
                page_found += 1
                maybe_click_see_more(driver, block)
                if i == 0:
                    time.sleep(0.5)

                reviewer = extract_reviewer_name(block)
                text = extract_review_text(block)
                # Skip teaser/empty reviews to avoid blank cards
                if not text or text.endswith("See more"):
                    continue
                review_link = extract_review_permalink(block)
                if review_link:
                    page_links_for_signature.append(canonical_link(review_link))

                try:
                    date_el = block.find_element(By.TAG_NAME, "time")
                    date_str = date_el.get_attribute("datetime").split("T")[0]
                    review_date = datetime.strptime(date_str, "%Y-%m-%d")
                except Exception:
                    # If date unavailable, do not add; also this makes signature less likely to be identical
                    continue

                if cutoff_date and review_date >= cutoff_date:
                    page_all_older_than_cutoff = False
                # Date interval filtering
                if start_dt and review_date < start_dt:
                    continue
                if end_dt and review_date > end_dt:
                    continue

                # Keyword filter logic (AND + OR)
                add_review = True
                if keywords:
                    if keyword_and and not all(k.lower() in text.lower() for k in keyword_and):
                        add_review = False
                    if keyword_or and not any(k.lower() in text.lower() for k in keyword_or):
                        add_review = False if not keyword_and else add_review

                if not add_review:
                    continue

                key = stable_review_key(reviewer, review_date.strftime("%Y-%m-%d"), review_link or "", text)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                reviews.append({
                    "reviewer": reviewer,
                    "date": review_date.strftime("%Y-%m-%d"),
                    "link": canonical_link(review_link),
                    "text": text
                })
                page_added += 1
                print(f"✅ Added review ({review_date.strftime('%Y-%m-%d')}): {text[:60]}...")

            print(f"📄 Page {page}: found {page_found} cards, added {page_added} reviews (total: {len(reviews)})")

            save_reviews(reviews, filename_base, mode, max_pages, months, keywords, start_date=start_date, end_date=end_date)

            # Build a stable signature of this page's content to detect last page loops
            page_signature = tuple(page_links_for_signature[:20])  # top 20 links are enough
            if previous_page_signature is not None and page_signature == previous_page_signature:
                same_page_signature_count += 1
            else:
                same_page_signature_count = 0
            previous_page_signature = page_signature

            # Update termination heuristics conservatively
            if page_added == 0:
                consecutive_no_additions += 1
            else:
                consecutive_no_additions = 0

            # Mode-specific stopping rules
            should_stop = False
            if mode == 1:
                # If 'all' pages was requested (max_pages is None), stop when we clearly loop
                if not max_pages:
                    if same_page_signature_count >= 1 or consecutive_no_additions >= max_consecutive_no_additions:
                        should_stop = True
            elif mode == 2:
                # Stop once we encounter pages entirely older than cutoff for a couple iterations
                if cutoff_date and page_all_older_than_cutoff and (page_added == 0 or consecutive_no_additions >= 1):
                    # Seeing a fully-out-of-range page with no additions means we're past the window
                    should_stop = True
                # Also stop on repeated identical pages
                if same_page_signature_count >= 1:
                    should_stop = True
            elif mode == 3:
                # For keywords, after several pages with no matches or repeated same page, stop
                if consecutive_no_additions >= max_consecutive_no_additions or same_page_signature_count >= 1:
                    should_stop = True

            page += 1
            time.sleep(1.2)

            if should_stop:
                print("⏹ Stopping based on end-of-results detection.")
                break

    except KeyboardInterrupt:
        interrupted = True
        print("\n🛑 Interrupted by user.")

    finally:
        driver.quit()
        if 'interrupted' in locals() and interrupted:
            print(f"ℹ️ Collected {len(reviews)} reviews before interrupt.")
        save_reviews(reviews, filename_base, mode, max_pages, months, keywords, start_date=start_date, end_date=end_date)

    return reviews


# ----------------------- Main -----------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Trustpilot scraper")
    parser.add_argument("--url", dest="url", default="https://www.trustpilot.com/review/www.europcar.co.uk?stars=1")
    parser.add_argument("--pages", dest="pages", default="", help="Number of pages (or 'all')")
    parser.add_argument("--months", dest="months", type=str, default="", help="Months back")
    parser.add_argument("--keywords", dest="keywords", type=str, default="", help="Comma separated keywords")
    parser.add_argument("--resume", dest="resume", action="store_true")
    parser.add_argument("--resume_file", dest="resume_file", type=str, default="", help="Path to prior results.json")
    parser.add_argument("--start_date", dest="start_date", type=str, default="", help="Start date YYYY-MM-DD")
    parser.add_argument("--end_date", dest="end_date", type=str, default="", help="End date YYYY-MM-DD")
    args = parser.parse_args()

    base_url = args.url
    resume = bool(args.resume)

    # Determine mode based on provided arguments
    if args.pages:
        pages_in = args.pages.strip().lower()
        max_pages = None if pages_in == "all" else int(pages_in)
        scrape_reviews(base_url, mode=1, max_pages=max_pages, resume=resume, resume_file=args.resume_file or None)
    elif args.months:
        months_in = int(args.months)
        scrape_reviews(base_url, mode=2, months=months_in, resume=resume, resume_file=args.resume_file or None)
    elif args.keywords:
        kws = [w.strip() for w in args.keywords.split(",") if w.strip()]
        scrape_reviews(base_url, mode=3, keywords=kws, resume=resume, resume_file=args.resume_file or None)
    elif args.start_date and args.end_date:
        scrape_reviews(base_url, mode=4, start_date=args.start_date, end_date=args.end_date, resume=resume, resume_file=args.resume_file or None)
    else:
        # Default: pages=all
        scrape_reviews(base_url, mode=1, max_pages=None, resume=resume, resume_file=args.resume_file or None)


