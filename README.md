# Hot Wheels Collection Manager

A small FastAPI application for tracking a personal Hot Wheels collection. The data lives in plain CSV files and the web interface is built with Jinja2 templates.

## Features
- Add individual models via a simple form
- Import multiple models in bulk
- Browse your collection with search and total counts
- Compare your collection to the master list and see missing items

## Requirements
- Python 3.10+
- pip

Install dependencies with:
```bash
pip install -r requirements.txt
```

## Running the Server
Start the development server with Uvicorn:
```bash
uvicorn app.main:app --reload
```
Then open `http://localhost:8000/form` in your browser to begin adding models.

## Data Files
The application expects two CSV files under `app/data`:
- `DONE_HotWheels1_commas.csv` – master list of all models
- `HotWheelsGitCollection.csv` – your personal collection (created automatically if missing)

## Notable Endpoints
- **GET `/form`** – HTML form to add a model
- **POST `/collect_bulk`** – submit a list of model numbers
- **GET `/collection`** – view your collection
- **GET `/lost`** – list models you do not own yet
- **GET `/compare`** – summary progress by series and year

All endpoints are available once the server is running.
