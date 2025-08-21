import argparse
import csv
import glob
import json
import os
import re
import sys
from typing import Dict, Iterable, List, Tuple

try:
    from bs4 import BeautifulSoup  # type: ignore
    HAS_BS4 = True
except Exception:
    HAS_BS4 = False


def _normalize_whitespace(text: str) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.strip()).lower()


def _canonical_link(link: str) -> str:
    if not link:
        return ""
    # Remove tracking/query params to make links comparable
    return link.split("?")[0].strip()


def _review_key(reviewer: str, date: str, link: str, text: str) -> Tuple[str, str, str, str]:
    # Use a robust key that survives missing links: prefer link, but also include
    # reviewer/date/text so repeating content with different page URLs is caught.
    return (
        _normalize_whitespace(reviewer),
        (_normalize_whitespace(date)),
        _canonical_link(link),
        _normalize_whitespace(text),
    )


def _dedupe_records(records: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    unique: List[Dict[str, str]] = []
    for r in records:
        reviewer = r.get("reviewer", "")
        date = r.get("date", "")
        link = r.get("link", "")
        text = r.get("text", "")
        key = _review_key(reviewer, date, link, text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


def dedupe_csv(path: str, backup: bool = True) -> Tuple[int, int]:
    if not os.path.exists(path):
        return (0, 0)
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    before = len(rows)
    rows = _dedupe_records(rows)
    after = len(rows)
    if backup:
        _write_backup(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["reviewer", "date", "link", "text"])
        writer.writeheader()
        writer.writerows(rows)
    return before, after


def dedupe_json(path: str, backup: bool = True) -> Tuple[int, int]:
    if not os.path.exists(path):
        return (0, 0)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    reviews = list(data.get("reviews", []))
    before = len(reviews)
    reviews = _dedupe_records(reviews)
    after = len(reviews)
    if backup:
        _write_backup(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"reviews": reviews}, f, indent=2, ensure_ascii=False)
    return before, after


def dedupe_html(path: str, backup: bool = True) -> Tuple[int, int]:
    if not os.path.exists(path):
        return (0, 0)
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    if not HAS_BS4:
        print(
            "[warn] beautifulsoup4 not installed; HTML dedupe will be skipped for",
            os.path.basename(path),
        )
        return (0, 0)

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.card")
    before = len(cards)

    seen = set()
    removed = 0
    for card in list(cards):
        reviewer_el = card.select_one(".reviewer")
        date_el = card.select_one(".date")
        link_el = card.select_one(".link a")
        text_el = card.select_one(".text")

        reviewer = reviewer_el.get_text(strip=True) if reviewer_el else ""
        date = date_el.get_text(strip=True) if date_el else ""
        link = link_el.get("href", "") if link_el else ""
        text = text_el.get_text("\n", strip=True) if text_el else ""

        key = _review_key(reviewer, date, link, text)
        if key in seen:
            card.decompose()
            removed += 1
        else:
            seen.add(key)

    after = before - removed

    # Update summary count if present
    summary = soup.select_one(".summary")
    if summary is not None:
        # Replace the number after 'Total reviews scraped:' with the new count
        for node in summary.contents:
            if isinstance(node, str) and "Total reviews scraped:" in node:
                # not typical; usually wrapped in <strong>. Keep generic approach below
                pass
        # More robust: find the <strong> label then the following text node with a number
        # but simpler is to regex against the HTML string for that block.
        summary_html = str(summary)
        summary_html = re.sub(
            r"(<strong>Total reviews scraped:</strong>\s*)\d+",
            rf"\g<1>{after}",
            summary_html,
        )
        new_summary = BeautifulSoup(summary_html, "html.parser")
        summary.replace_with(new_summary)

    if backup:
        _write_backup(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(soup))

    return before, after


def _write_backup(path: str) -> None:
    backup_path = path + ".bak"
    if not os.path.exists(backup_path):
        with open(path, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())


def _collect_targets(patterns: List[str]) -> List[str]:
    files: List[str] = []
    for p in patterns:
        files.extend(sorted(glob.glob(p)))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deduplicate Trustpilot scan outputs (CSV, JSON, HTML)."
    )
    parser.add_argument(
        "--files",
        nargs="*",
        help=(
            "Specific files to dedupe. If omitted, processes europcar_reviews_*.{csv,json,html}."
        ),
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak backups before overwriting files",
    )
    args = parser.parse_args()

    backup = not args.no_backup
    targets: List[str]
    if args.files:
        targets = args.files
    else:
        targets = _collect_targets(
            [
                "europcar_reviews_*.csv",
                "europcar_reviews_*.json",
                "europcar_reviews_*.html",
            ]
        )

    if not targets:
        print("No files found to process.")
        return 0

    total_removed = 0
    for path in targets:
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".csv":
                before, after = dedupe_csv(path, backup=backup)
            elif ext == ".json":
                before, after = dedupe_json(path, backup=backup)
            elif ext == ".html":
                before, after = dedupe_html(path, backup=backup)
            else:
                print(f"[skip] Unsupported file type: {path}")
                continue
            removed = max(0, before - after)
            total_removed += removed
            print(f"[ok] {os.path.basename(path)}: {before} -> {after} (removed {removed})")
        except Exception as e:
            print(f"[error] Failed to process {path}: {e}")

    if total_removed == 0:
        print("No duplicates found.")
    else:
        print(f"Done. Removed {total_removed} duplicate entries across files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


