from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import csv
import os
import io
import re
from typing import Dict, List

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
COLLECTION_FILE = os.path.join(DATA_DIR, 'HotWheelsGitCollection.csv')
MASTER_FILE = os.path.join(DATA_DIR, 'DONE_HotWheels1_commas.csv')

REQUIRED_FIELDS = ["toy_number", "name", "year", "series", "image_url", "quantity"]

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), 'templates'))
app = FastAPI()

class CSVCache:
    def __init__(self, path: str):
        self.path = path
        self.mtime = None
        self.data: List[Dict[str, str]] = []

    def load(self) -> List[Dict[str, str]]:
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail=f"File not found: {self.path}")
        if self.mtime != stat.st_mtime:
            with open(self.path, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames
                if headers is None or any(h not in REQUIRED_FIELDS for h in headers):
                    raise HTTPException(status_code=500, detail="Invalid CSV headers")
                self.data = []
                for row in reader:
                    cleaned = {k: row.get(k, '').strip() for k in REQUIRED_FIELDS}
                    if not cleaned['quantity']:
                        cleaned['quantity'] = '0'
                    self.data.append(cleaned)
            self.mtime = stat.st_mtime
        return self.data

    def save(self, rows: List[Dict[str, str]]):
        with open(self.path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=REQUIRED_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        self.mtime = os.stat(self.path).st_mtime
        self.data = rows

collection_cache = CSVCache(COLLECTION_FILE)
master_cache = CSVCache(MASTER_FILE)

SERIES_CLEAN_RE = re.compile(r"(New for \d{4}|2nd Color|Exclusive)", re.IGNORECASE)

def normalize_series(series: str) -> str:
    return SERIES_CLEAN_RE.sub('', series).strip()

# Utility functions

def find_in_master(toy_number: str) -> Dict[str, str]:
    for row in master_cache.load():
        if row['toy_number'].upper() == toy_number.upper():
            return row
    return {}


def add_or_update_model(toy_number: str, quantity: int) -> Dict[str, str]:
    master_row = find_in_master(toy_number)
    if not master_row:
        raise HTTPException(status_code=400, detail="Invalid toy_number")
    rows = collection_cache.load()
    for row in rows:
        if row['toy_number'] == master_row['toy_number'] and row['image_url'] == master_row['image_url']:
            q = int(row.get('quantity', '1')) + quantity
            row['quantity'] = str(max(q, 1))
            collection_cache.save(rows)
            return row
    new_row = {
        'toy_number': master_row['toy_number'],
        'name': master_row['name'],
        'year': master_row['year'],
        'series': master_row['series'],
        'image_url': master_row['image_url'],
        'quantity': str(max(quantity, 1)),
    }
    rows.append(new_row)
    collection_cache.save(rows)
    return new_row

@app.get('/form', response_class=HTMLResponse)
async def form(request: Request):
    return templates.TemplateResponse('form.html', {'request': request})

@app.post('/collect_form')
async def collect_form(toy_number: str = Form(...), quantity: int = Form(1)):
    try:
        row = add_or_update_model(toy_number, quantity)
    except HTTPException as e:
        return JSONResponse({'status': 'error', 'reason': e.detail})
    return {'status': 'ok', 'added': row}

@app.post('/collect_bulk')
async def collect_bulk(text: str = Form(...)):
    entries = re.findall(r'(?:x?(\d+)\s*)?([A-Za-z0-9]+)', text)
    if not entries:
        return {'status': 'error', 'reason': 'No entries found'}
    results = []
    for qty, toy in entries:
        q = int(qty) if qty else 1
        try:
            row = add_or_update_model(toy, q)
            results.append(row)
        except HTTPException:
            continue
    return {'status': 'ok', 'added': results}

@app.get('/collection', response_class=HTMLResponse)
async def show_collection(request: Request):
    rows = collection_cache.load()
    return templates.TemplateResponse('collection.html', {'request': request, 'rows': rows})

@app.get('/lost')
async def lost():
    master = master_cache.load()
    collection = collection_cache.load()
    coll_set = set(row['toy_number'] for row in collection)
    missing = [row for row in master if row['toy_number'] not in coll_set]
    return {'status': 'ok', 'missing': missing}

@app.get('/compare')
async def compare():
    master = master_cache.load()
    collection = collection_cache.load()
    series_map: Dict[str, Dict[str, int]] = {}
    for row in master:
        key = f"{normalize_series(row['series'])} {row['year']}"
        series_map.setdefault(key, {'total': 0, 'owned': 0})
        series_map[key]['total'] += 1
    for row in collection:
        key = f"{normalize_series(row['series'])} {row['year']}"
        if key in series_map:
            series_map[key]['owned'] += 1
    return {'status': 'ok', 'progress': series_map}

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
            q = max(int(row['quantity']) + delta, 1)
            row['quantity'] = str(q)
            collection_cache.save(rows)
            return {'status': 'ok', 'new_quantity': q}
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
