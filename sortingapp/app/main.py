import os
import re
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple

import orjson
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, UploadFile, Form, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sentence_transformers import SentenceTransformer, util
import torch


# Paths
# APP_DIR = .../sortingapp/app
APP_DIR = Path(__file__).resolve().parent
# PROJECT_DIR = .../sortingapp
PROJECT_DIR = APP_DIR.parent
# DATA_DIR = .../trustpilot (parent of sortingapp)
DATA_DIR = PROJECT_DIR.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


app = FastAPI(title="Review Sorting App")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def list_result_files() -> List[Path]:
    patterns = ["europcar_reviews_*.csv", "europcar_reviews_*.json", "europcar_reviews_*.html"]
    files: List[Path] = []
    for pat in patterns:
        files.extend(sorted(DATA_DIR.glob(pat)))
    return files


def read_reviews(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    elif path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        df = pd.DataFrame(data.get("reviews", []))
    elif path.suffix.lower() == ".html":
        with open(path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        cards = soup.select("div.card")
        rows = []
        for c in cards:
            reviewer = c.select_one(".reviewer")
            date = c.select_one(".date")
            link = c.select_one(".link a")
            text = c.select_one(".text")
            rows.append({
                "reviewer": reviewer.get_text(strip=True) if reviewer else "",
                "date": date.get_text(strip=True) if date else "",
                "link": link.get("href") if link else "",
                "text": text.get_text("\n", strip=True) if text else "",
            })
        df = pd.DataFrame(rows)
    else:
        raise ValueError("Unsupported file type")

    # Normalize columns
    for col in ["reviewer", "date", "link", "text"]:
        if col not in df.columns:
            df[col] = ""
    # Coerce date
    with np.errstate(all="ignore"):
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df[["reviewer", "date", "link", "text"]]


# Model configurations - name, size, description
MODEL_CONFIGS = {
    "fast": {
        "name": "sentence-transformers/all-MiniLM-L6-v2",
        "size": "~90MB",
        "description": "Fast - Good for quick searches",
        "batch_size": 64
    },
    "balanced": {
        "name": "sentence-transformers/all-mpnet-base-v2",
        "size": "~1.6GB",
        "description": "Balanced - Best speed/accuracy trade-off (your yesterday's model)",
        "batch_size": 32
    },
    "precise": {
        "name": "sentence-transformers/all-mpnet-base-v2",
        "size": "~1.6GB",
        "description": "Precise - Highest accuracy, smaller batches for precision",
        "batch_size": 16
    }
}

# Global model cache
MODEL_CACHE = {}

def load_model(model_type: str = "fast") -> SentenceTransformer:
    """Load a model, caching it for reuse"""
    if model_type not in MODEL_CONFIGS:
        model_type = "fast"  # fallback

    if model_type in MODEL_CACHE:
        print(f"Using cached {model_type} model")
        return MODEL_CACHE[model_type]

    config = MODEL_CONFIGS[model_type]
    print(f"Loading {model_type} model: {config['name']} ({config['size']})")
    print(f"This may take a few minutes on first use...")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    try:
        model = SentenceTransformer(config["name"], device=device)
        MODEL_CACHE[model_type] = model
        print(f"✅ {model_type} model loaded successfully!")
        return model
    except Exception as e:
        print(f"❌ Error loading {model_type} model: {e}")
        print("Falling back to fast model...")
        return load_model("fast")

# Load default model
DEFAULT_MODEL = "fast"
MODEL = load_model(DEFAULT_MODEL)


def semantic_filter(df: pd.DataFrame, query: str, model_type: str = "fast") -> Tuple[pd.DataFrame, np.ndarray]:
    # Get the appropriate model
    model = load_model(model_type)
    config = MODEL_CONFIGS[model_type]

    texts = df["text"].fillna("").astype(str).tolist()
    print(f"Processing {len(texts)} reviews with {model_type} model ({config['name']})...")
    print(f"Model size: {config['size']}, Batch size: {config['batch_size']}")

    # Batch encode with progress
    emb_reviews = model.encode(
        texts,
        batch_size=config['batch_size'],
        convert_to_tensor=True,
        show_progress_bar=True,  # Show progress bar
        normalize_embeddings=True
    )
    print("Encoding query...")
    emb_query = model.encode([query], convert_to_tensor=True, normalize_embeddings=True)

    print("Computing similarity scores...")
    scores = util.cos_sim(emb_query, emb_reviews).cpu().numpy()[0]

    df = df.copy()
    df["score"] = scores
    print(f"AI processing complete. Score range: {scores.min():.3f} to {scores.max():.3f}")
    return df, scores


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    files = list_result_files()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "files": files,
        "default_threshold": 0.35,
        "model_configs": MODEL_CONFIGS,
        "default_model": DEFAULT_MODEL
    })

@app.get("/landing", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})

@app.get("/scrape", response_class=HTMLResponse)
async def scrape_page(request: Request):
    return templates.TemplateResponse("scrape.html", {"request": request})

@app.post("/scrape/run")
async def scrape_run(
    company_url: str = Form(...),
    mode: str = Form("pages"),
    max_pages: str = Form(""),
    months: str = Form(""),
    keywords: str = Form(""),
    resume: str = Form("no"),
):
    import subprocess, shlex
    # Build command
    script_path = str(APP_DIR / "scrape_reviews.py")
    args = ["python", script_path, "--url", company_url]
    if mode == "pages" and max_pages.strip():
        args += ["--pages", max_pages.strip()]
    if mode == "months" and months.strip():
        args += ["--months", months.strip()]
    if mode == "keywords" and keywords.strip():
        args += ["--keywords", keywords.strip()]
    if resume == "yes":
        args += ["--resume"]

    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        output, _ = proc.communicate()
        code = proc.returncode
        return JSONResponse({"ok": code == 0, "code": code, "output": output, "cmd": args})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    file_path: str = Form(""),
    query: str = Form(...),
    sort_by: str = Form("score_desc"),
    threshold: float = Form(0.35),
    model_type: str = Form("fast"),
    upload: UploadFile | None = File(None)
):
    # Determine source: uploaded file takes precedence, otherwise path
    temp_uploaded: Path | None = None
    if upload is not None and upload.filename:
        temp_uploaded = (PROJECT_DIR / f"_uploaded_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{upload.filename}")
        content = await upload.read()
        with open(temp_uploaded, "wb") as f:
            f.write(content)
        src = temp_uploaded
    else:
        src = Path(file_path)
    df = read_reviews(src)
    filtered, scores = semantic_filter(df, query, model_type)
    # Apply threshold
    filtered = filtered[filtered["score"] >= float(threshold)]

    # Sorting
    if sort_by == "score_desc":
        filtered = filtered.sort_values(by=["score"], ascending=False)
    elif sort_by == "score_asc":
        filtered = filtered.sort_values(by=["score"], ascending=True)
    elif sort_by == "date_asc":
        filtered = filtered.sort_values(by=["date", "reviewer"], ascending=[True, True])
    elif sort_by == "date_desc":
        filtered = filtered.sort_values(by=["date", "reviewer"], ascending=[False, True])
    elif sort_by == "reviewer_asc":
        filtered = filtered.sort_values(by=["reviewer"], ascending=True)
    elif sort_by == "reviewer_desc":
        filtered = filtered.sort_values(by=["reviewer"], ascending=False)

    # Render results
    meta = {
        "source": src.name,
        "source_path": str(src),
        "query": query,
        "count": int(len(filtered)),
        "threshold": float(threshold),
        "sort_by": sort_by,
        "model_type": model_type
    }
    rows = filtered.fillna("")
    return templates.TemplateResponse("results.html", {"request": request, "meta": meta, "rows": rows.to_dict(orient="records")})


@app.post("/export")
async def export(file_path: str = Form(...), query: str = Form(...), sort_by: str = Form("score_desc"), threshold: float = Form(0.35), model_type: str = Form("fast")):
    src = Path(file_path)
    df = read_reviews(src)
    filtered, _ = semantic_filter(df, query, model_type)
    filtered = filtered[filtered["score"] >= float(threshold)]

    # Apply same sort as UI
    if sort_by == "score_desc":
        filtered = filtered.sort_values(by=["score"], ascending=False)
    elif sort_by == "score_asc":
        filtered = filtered.sort_values(by=["score"], ascending=True)
    elif sort_by == "date_asc":
        filtered = filtered.sort_values(by=["date", "reviewer"], ascending=[True, True])
    elif sort_by == "date_desc":
        filtered = filtered.sort_values(by=["date", "reviewer"], ascending=[False, True])
    elif sort_by == "reviewer_asc":
        filtered = filtered.sort_values(by=["reviewer"], ascending=True)
    elif sort_by == "reviewer_desc":
        filtered = filtered.sort_values(by=["reviewer"], ascending=False)

    # Prepare in-memory ZIP for browser download so user picks location
    import io, zipfile
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_query = re.sub(r"[^a-zA-Z0-9_\-]+", "_", query.strip())[:120]
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        # CSV
        csv_bytes = filtered[["reviewer", "date", "link", "text", "score"]].to_csv(index=False).encode("utf-8")
        z.writestr("results.csv", csv_bytes)
        # JSON
        json_bytes = json.dumps({
            "source": src.name,
            "query": query,
            "threshold": float(threshold),
            "reviews": filtered[["reviewer", "date", "link", "text", "score"]].fillna("").to_dict(orient="records")
        }, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        z.writestr("results.json", json_bytes)
        # HTML
        html_items = []
        for r in filtered.fillna("").to_dict(orient="records"):
            html_items.append(f"""
            <div class='card'>
                <div class='reviewer'>{r['reviewer']}</div>
                <div class='date'>{r['date']}</div>
                <div class='score'>Match score: {r.get('score', 0):.3f}</div>
                <div class='link'><a href='{r['link']}' target='_blank'>{r['link']}</a></div>
                <div class='text'>{r['text']}</div>
            </div>
            """)
        html_doc = f"""
        <html>
        <head>
            <meta charset='utf-8'>
            <title>Exported Results</title>
            <style>
                body {{ font-family: Arial, sans-serif; background: #f7fafc; padding: 20px; }}
                h1 {{ text-align: center; color: #2d3748; }}
                .summary {{ max-width: 1000px; margin: 0 auto 20px; background: #edf2f7; padding: 15px; border-radius: 8px; }}
                .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 15px; margin: 15px auto; max-width: 1000px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}
                .reviewer {{ font-weight: bold; color: #2d3748; }}
                .date {{ color: #718096; font-size: 0.9em; margin-bottom: 4px; }}
                .score {{ color: #805ad5; font-size: 0.9em; margin-bottom: 8px; }}
                .link a {{ color: #3182ce; text-decoration: none; }}
                .text {{ margin-top: 8px; line-height: 1.5; color: #1a202c; white-space: pre-wrap; }}
            </style>
        </head>
        <body>
            <h1>Exported Results</h1>
            <div class='summary'>
                <strong>Source:</strong> {src.name}<br>
                <strong>Query:</strong> {query}<br>
                <strong>Threshold:</strong> {float(threshold)}<br>
                <strong>Total:</strong> {len(filtered)}
            </div>
            {''.join(html_items)}
        </body>
        </html>
        """
        z.writestr("results.html", html_doc)
    zip_buf.seek(0)
    filename = f"export_{safe_query}_{ts}.zip"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return StreamingResponse(zip_buf, media_type="application/zip", headers=headers)

@app.post("/shutdown")
async def shutdown():
    # Return response first, then exit
    import threading, time as _t, os as _os, sys as _sys
    def _exit_later():
        _t.sleep(0.5)
        _os._exit(0)
    threading.Thread(target=_exit_later, daemon=True).start()
    return {"status": "shutting down"}


