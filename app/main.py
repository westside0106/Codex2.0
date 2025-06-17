from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from typing import List, Dict
import csv
import os
import io
import re

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
COLLECTION_FILE = os.path.join(DATA_DIR, 'HotWheelsGitCollection.csv')
MASTER_FILE = os.path.join(DATA_DIR, 'DONE_HotWheels1_commas.csv')
REQUIRED_FIELDS = ["toy_number", "name", "year", "series", "image_url", "quantity"]

app = FastAPI(title="Hot Wheels Collection")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, 'templates'))
app.mount('/static', StaticFiles(directory=os.path.join(BASE_DIR, 'static')), name='static')

SERIES_CLEAN_RE = re.compile(r"(New for \d{4}|2nd Color|Exclusive)", re.IGNORECASE)


def normalize_series(series: str) -> str:
    """Remove special tags from series string."""
    return SERIES_CLEAN_RE.sub('', series).strip()


class CSVCache:
    """Simple cache that reloads CSV when the file changes."""

    def __init__(self, path: str):
        self.path = path
        self.mtime: float | None = None
        self.data: List[Dict[str, str]] = []
        self.ensure_file()

    def ensure_file(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            with open(self.path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=REQUIRED_FIELDS)
                writer.writeheader()
            self.mtime = os.path.getmtime(self.path)

    def load(self) -> List[Dict[str, str]]:
        self.ensure_file()
        stat = os.stat(self.path)
        if self.mtime != stat.st_mtime:
            with open(self.path, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != REQUIRED_FIELDS:
                    # auto-fix invalid headers
                    rows = [row for row in reader]
                    self.save(rows)
                else:
                    self.data = [{k: row.get(k, '').strip() for k in REQUIRED_FIELDS} for row in reader]
            self.mtime = stat.st_mtime
        return self.data

    def save(self, rows: List[Dict[str, str]]) -> None:
        self.ensure_file()
        with open(self.path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=REQUIRED_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        self.mtime = os.path.getmtime(self.path)
        self.data = rows


collection_cache = CSVCache(COLLECTION_FILE)
master_cache = CSVCache(MASTER_FILE)


# ---------------------- helper functions ----------------------

def find_in_master(toy_number: str) -> Dict[str, str] | None:
    toy_number = toy_number.upper().strip()
    for row in master_cache.load():
        if row['toy_number'].upper() == toy_number:
            return row
    return None


def add_or_update_model(toy_number: str, quantity: int) -> Dict[str, str]:
    if quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be positive")
    master_row = find_in_master(toy_number)
    if not master_row:
        raise HTTPException(status_code=400, detail="Invalid toy_number")

    rows = collection_cache.load()
    for row in rows:
        if row['toy_number'] == master_row['toy_number'] and row['image_url'] == master_row['image_url']:
            new_q = max(int(row.get('quantity', '1')) + quantity, 1)
            row['quantity'] = str(new_q)
            collection_cache.save(rows)
            return row

    new_row = {k: master_row[k] for k in REQUIRED_FIELDS[:-1]}
    new_row['quantity'] = str(quantity)
    rows.append(new_row)
    collection_cache.save(rows)
    return new_row


def parse_bulk(text: str) -> List[tuple[str, int]]:
    pattern = re.compile(r'(?:x?(\d+)\s*)?([A-Za-z0-9]+)')
    return [(toy.upper(), int(qty) if qty else 1) for qty, toy in pattern.findall(text)]


def progress_map() -> Dict[str, Dict[str, int]]:
    master = master_cache.load()
    collection = collection_cache.load()
    prog: Dict[str, Dict[str, int]] = {}
    for row in master:
        key = f"{normalize_series(row['series'])} {row['year']}"
        prog.setdefault(key, {'total': 0, 'owned': 0})
        prog[key]['total'] += 1
    for row in collection:
        key = f"{normalize_series(row['series'])} {row['year']}"
        if key in prog:
            prog[key]['owned'] += 1
    return prog


# --------------------------- routes ---------------------------

@app.get('/form', response_class=HTMLResponse)
async def form(request: Request):
    """Main entry page with forms to add models."""
    return templates.TemplateResponse('form.html', {'request': request, 'title': 'Add Model'})


@app.post('/collect_form')
async def collect_form(toy_number: str = Form(...), quantity: int = Form(1)):
    try:
        row = add_or_update_model(toy_number, quantity)
        return {'status': 'ok', 'added': row}
    except HTTPException as e:
        return JSONResponse({'status': 'error', 'reason': e.detail})


@app.post('/collect_bulk')
async def collect_bulk(text: str = Form(...)):
    entries = parse_bulk(text)
    if not entries:
        return {'status': 'error', 'reason': 'No valid entries found'}
    added = []
    for toy, qty in entries:
        try:
            row = add_or_update_model(toy, qty)
            added.append(row)
        except HTTPException:
            continue
    return {'status': 'ok', 'added': added}


@app.get('/collection', response_class=HTMLResponse)
async def show_collection(request: Request, q: str | None = None):
    rows = collection_cache.load()
    if q:
        q_low = q.lower()
        rows = [r for r in rows if q_low in r['toy_number'].lower() or q_low in r['name'].lower()]
    total = sum(int(r['quantity']) for r in rows)
    context = {'request': request, 'rows': rows, 'total': total, 'q': q}
    return templates.TemplateResponse('collection.html', context)


@app.get('/lost', response_class=HTMLResponse)
async def lost(request: Request):
    master = master_cache.load()
    collection = {row['toy_number'] for row in collection_cache.load()}
    missing = [row for row in master if row['toy_number'] not in collection]
    return templates.TemplateResponse('lost.html', {'request': request, 'rows': missing})


@app.get('/compare', response_class=HTMLResponse)
async def compare(request: Request):
    return templates.TemplateResponse('compare.html', {'request': request, 'progress': progress_map()})


@app.get('/toy_info')
async def toy_info(toy_number: str):
    row = find_in_master(toy_number)
    if not row:
        return {'status': 'error', 'reason': 'Not found'}
    return {'status': 'ok', 'info': row}


@app.post('/adjust_quantity')
async def adjust_quantity(toy_number: str = Form(...), delta: int = Form(...)):
    rows = collection_cache.load()
    for row in rows:
        if row['toy_number'] == toy_number:
            new_q = max(int(row['quantity']) + delta, 1)
            row['quantity'] = str(new_q)
            collection_cache.save(rows)
            return {'status': 'ok', 'new_quantity': new_q}
    return {'status': 'error', 'reason': 'Model not found'}


@app.post('/delete_model')
async def delete_model(toy_number: str = Form(...)):
    rows = collection_cache.load()
    new_rows = [r for r in rows if r['toy_number'] != toy_number]
    if len(new_rows) == len(rows):
        return {'status': 'error', 'reason': 'Model not found'}
    collection_cache.save(new_rows)
    return {'status': 'ok'}


@app.get('/download_csv')
async def download_csv():
    rows = collection_cache.load()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=REQUIRED_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename=collection.csv'})


@app.get('/json')
async def get_json():
    return collection_cache.load()


# -------------------- admin/cache endpoints --------------------

@app.post('/admin/reload')
async def admin_reload(file: str = Form(...)):
    if file == 'master':
        master_cache.mtime = None
        master_cache.load()
    elif file == 'collection':
        collection_cache.mtime = None
        collection_cache.load()
    else:
        raise HTTPException(status_code=400, detail='unknown file')
    return {'status': 'ok'}


@app.get('/admin/cache_status')
async def cache_status():
    return {
        'collection_mtime': collection_cache.mtime,
        'master_mtime': master_cache.mtime,
    }
