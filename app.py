from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from flask import Flask, jsonify, request, send_file, send_from_directory, url_for
from openpyxl import load_workbook
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable, Image, KeepInFrame, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)

# Google Drive is optional while developing locally.
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build as google_build
    from googleapiclient.http import MediaIoBaseDownload
except Exception:
    service_account = None
    google_build = None
    MediaIoBaseDownload = None


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
SOURCE_DIR = DATA_DIR / "source_files"
REPORT_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "mms_system.sqlite3"

DATA_DIR.mkdir(exist_ok=True)
SOURCE_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


@app.after_request
def add_cors_headers(response):
    cors_paths = ("/api/", "/report-download/", "/report-preview/", "/report-attachment/", "/source-file/", "/health", "/setup-info")
    if request.path.startswith(cors_paths):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
    return response


def serve_frontend_file(relative_path: str):
    path = FRONTEND_DIR / relative_path
    if not path.exists():
        return "Frontend file not found.", 404
    return send_file(path)


# Frontend files now live under frontend/. Flask exposes backend APIs and file downloads.
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SYNC_INTERVAL_SECONDS = 0
ENABLE_BACKGROUND_SYNC = False  # manual sync only
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()

SOURCE_TYPES = {
    "BOOK_MASTER": {
        "label": "Books Master",
        "required": ["MMS Code", "BAV Code", "Book Name", "Language", "Language Category", "Edition Year", "Reprint Year", "Author"],
    },
    "BOOK_STOCK": {
        "label": "Books Stock Position",
        "required": ["MMS Code"],
    },
    "AUDIO_MASTER": {
        "label": "Audio Master",
        "required": ["Item Number (MMS)", "Item Details", "Old/Latest", "Type"],
    },
    "AUDIO_STOCK": {
        "label": "Audio Stock Position",
        "required": ["MMS Code"],
    },
    "PHOTO_MASTER": {
        "label": "Photos Master",
        "required": ["Item Number (MMS)", "Item Details", "Old/Latest", "Photo type"],
    },
    "PHOTO_STOCK": {
        "label": "Photos Stock Position",
        "required": ["MMS Code"],
    },
    "SOSRC": {
        "label": "SOSRC Book List",
        "required": [],
    },
    "PREVIOUS_INDENT": {
        "label": "Previous Indent",
        "required": [],
    },
}

REPORT_LABELS = {
    "new-releases": "New Releases",
    "book-catalog": "Book Catalog",
    "out-of-stock": "Out of Stock Books",
    "mms-indent": "MMS Indent",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS sources (
                source_type TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                drive_file_id TEXT,
                drive_file_name TEXT,
                drive_modified_time TEXT,
                active_local_path TEXT,
                active_version_id INTEGER,
                record_count INTEGER DEFAULT 0,
                last_sync_attempt TEXT,
                last_successful_sync TEXT,
                last_status TEXT DEFAULT 'NOT_CONFIGURED',
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS source_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                drive_file_id TEXT,
                drive_file_name TEXT,
                drive_modified_time TEXT,
                synced_at TEXT NOT NULL,
                status TEXT NOT NULL,
                record_count INTEGER DEFAULT 0,
                local_path TEXT,
                error_message TEXT,
                FOREIGN KEY(source_type) REFERENCES sources(source_type)
            );

            CREATE TABLE IF NOT EXISTS books_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mms_code TEXT,
                bav_code TEXT,
                short_code TEXT,
                book_name TEXT,
                lang_code TEXT,
                language TEXT,
                language_category TEXT,
                reprint_year TEXT,
                edition_year TEXT,
                no_of_copies TEXT,
                cost_price REAL,
                sale_price REAL,
                check_value TEXT,
                ver_max TEXT,
                identity TEXT,
                classification TEXT,
                category TEXT,
                ntp TEXT,
                author TEXT,
                society TEXT,
                category_v2 TEXT,
                source_version_id INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_books_mms ON books_master(mms_code);
            CREATE INDEX IF NOT EXISTS idx_books_language ON books_master(language);
            CREATE INDEX IF NOT EXISTS idx_books_category ON books_master(language_category);

            CREATE TABLE IF NOT EXISTS books_stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mms_code TEXT,
                total_hq_dl_stock REAL DEFAULT 0,
                pb_hq_dera_stalls REAL DEFAULT 0,
                raw_json TEXT,
                source_version_id INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_stock_mms ON books_stock(mms_code);

            CREATE TABLE IF NOT EXISTS audio_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mms_code TEXT,
                bav_code TEXT,
                short_code TEXT,
                item_details TEXT,
                old_latest TEXT,
                type TEXT,
                type_description TEXT,
                cost_price REAL,
                sale_price REAL,
                source_version_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                report_type TEXT NOT NULL,
                market TEXT NOT NULL,
                month INTEGER NOT NULL,
                year INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finalized_at TEXT,
                source_versions_json TEXT NOT NULL,
                original_json TEXT NOT NULL,
                draft_json TEXT NOT NULL,
                changes_json TEXT NOT NULL DEFAULT '[]',
                output_file TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_reports_type_date
                ON reports(report_type, year, month, created_at);

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        # Backward-compatible schema upgrades.
        def ensure_column(table, name, ddl):
            cols={r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if name not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        ensure_column("source_versions","diff_json","TEXT")
        ensure_column("source_versions","previous_version_id","INTEGER")
        ensure_column("source_versions","reviewed_at","TEXT")
        ensure_column("source_versions","decision","TEXT")
        ensure_column("reports","attachments_json","TEXT DEFAULT '{}'")
        ensure_column("reports","final_filename","TEXT")

        for source_type, cfg in SOURCE_TYPES.items():
            conn.execute(
                """
                INSERT INTO sources(source_type, label)
                VALUES(?, ?)
                ON CONFLICT(source_type) DO UPDATE SET label=excluded.label
                """,
                (source_type, cfg["label"]),
            )


init_db()


# ----------------------------- utility helpers -----------------------------

def normalize_header(value: Any) -> str:
    value = "" if value is None else str(value)
    value = value.replace("\n", " ").replace("\r", " ")
    value = re.sub(r"\s+", " ", value).strip().upper()
    return value


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def normalize_code(value: Any) -> str:
    return normalize_text(value).upper()


def normalize_mms(value: Any) -> str:
    s = normalize_text(value)
    if s.endswith(".0") and s[:-2].replace(".", "", 1).isdigit():
        s = s[:-2]
    return s


def short_code_from_bav(value: Any) -> str:
    return normalize_code(value)[:8]


def to_number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".", "-.") else 0.0
    except Exception:
        return 0.0


def year_matches(value: Any, year: int) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    return bool(re.search(rf"(?<!\d){re.escape(str(year))}(?!\d)", text))


def market_allows(language_category: str, market: str) -> bool:
    category = normalize_header(language_category)
    if market == "INDIAN":
        return category != "FOREIGN"
    return True


def source_row(source_type: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM sources WHERE source_type=?", (source_type,)).fetchone()
    return dict(row) if row else {}


def configured_sources() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM sources ORDER BY label").fetchall()
    return [dict(r) for r in rows]


def active_source_versions(types: list[str]) -> dict:
    result = {}
    with db() as conn:
        for source_type in types:
            row = conn.execute(
                """
                SELECT s.*, v.synced_at AS version_synced_at
                FROM sources s
                LEFT JOIN source_versions v ON v.id=s.active_version_id
                WHERE s.source_type=?
                """,
                (source_type,),
            ).fetchone()
            if row:
                result[source_type] = {
                    "source_type": source_type,
                    "label": row["label"],
                    "drive_file_name": row["drive_file_name"],
                    "drive_modified_time": row["drive_modified_time"],
                    "last_successful_sync": row["last_successful_sync"],
                    "record_count": row["record_count"],
                    "version_id": row["active_version_id"],
                    "status": row["last_status"],
                }
    return result


# ----------------------------- Google Drive -----------------------------

def get_drive_service():
    if not GOOGLE_SERVICE_ACCOUNT_FILE:
        raise RuntimeError(
            "Google Drive is not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE "
            "to a read-only service-account JSON file."
        )
    if service_account is None or google_build is None:
        raise RuntimeError(
            "Google Drive packages are missing. Run: "
            "pip install google-api-python-client google-auth"
        )
    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=[DRIVE_READONLY_SCOPE],
    )
    return google_build("drive", "v3", credentials=credentials, cache_discovery=False)


def drive_metadata(file_id: str) -> dict:
    service = get_drive_service()
    return service.files().get(
        fileId=file_id,
        fields="id,name,mimeType,modifiedTime,size,md5Checksum",
        supportsAllDrives=True,
    ).execute()


def drive_download(file_id: str, mime_type: str) -> bytes:
    service = get_drive_service()

    if mime_type == "application/vnd.google-apps.spreadsheet":
        request_obj = service.files().export_media(
            fileId=file_id,
            mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        request_obj = service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        )

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request_obj)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


# ----------------------------- spreadsheet parsing -----------------------------

def find_header_row(ws, required_groups: list[list[str]], scan_rows: int = 40) -> int:
    for row_index in range(1, min(ws.max_row, scan_rows) + 1):
        values = [normalize_header(ws.cell(row_index, c).value) for c in range(1, ws.max_column + 1)]
        values_set = set(values)
        if all(any(normalize_header(candidate) in values_set for candidate in group) for group in required_groups):
            return row_index
    return -1


def header_map(ws, header_row: int) -> dict[str, int]:
    out = {}
    for c in range(1, ws.max_column + 1):
        key = normalize_header(ws.cell(header_row, c).value)
        if key:
            out[key] = c
    return out


def find_column(hmap: dict[str, int], candidates: list[str]) -> int | None:
    for candidate in candidates:
        key = normalize_header(candidate)
        if key in hmap:
            return hmap[key]
    return None


def read_rows(ws, header_row: int) -> list[dict]:
    headers = [normalize_text(ws.cell(header_row, c).value) for c in range(1, ws.max_column + 1)]
    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        values = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        if not any(normalize_text(v) for v in values):
            continue
        row = {}
        for idx, header in enumerate(headers):
            if header:
                row[header] = values[idx]
        row["__raw"] = values
        rows.append(row)
    return rows


def load_book_master(path: Path) -> list[dict]:
    wb = load_workbook(path, read_only=False, data_only=True)
    ws = wb[wb.sheetnames[0]]
    header_row = find_header_row(
        ws,
        [
            ["MMS Code"],
            ["Book Name", "TITLE"],
            ["Language"],
            ["BAV Code", "CODE"],
        ],
        30,
    )
    if header_row < 0:
        raise ValueError("Could not detect Books Master header row.")

    hmap = header_map(ws, header_row)
    required = {
        "mms": find_column(hmap, ["MMS Code"]),
        "bav": find_column(hmap, ["BAV Code", "CODE"]),
        "title": find_column(hmap, ["Book Name", "TITLE"]),
        "language": find_column(hmap, ["Language"]),
        "lang_category": find_column(hmap, ["Language Category", "Laguage Category"]),
        "edition": find_column(hmap, ["Edition Year"]),
        "reprint": find_column(hmap, ["Reprint Year"]),
        "author": find_column(hmap, ["Author"]),
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError("Books Master is missing required columns: " + ", ".join(missing))

    optional = {
        "short_code": find_column(hmap, ["Short Code"]),
        "lang_code": find_column(hmap, ["Lang Code"]),
        "copies": find_column(hmap, ["No. of Copies"]),
        "cost": find_column(hmap, ["Cost Price(Rs.)", "Cost Price (Rs.)", "COST PRICE"]),
        "sale": find_column(hmap, ["Sale Price(Rs.)", "Sale Price (Rs.)", "SALE PRICE"]),
        "check": find_column(hmap, ["Check"]),
        "ver_max": find_column(hmap, ["VEr MAx", "Ver Max", "Max Version"]),
        "identity": find_column(hmap, ["Identity"]),
        "classification": find_column(hmap, ["Classification"]),
        "category": find_column(hmap, ["Category"]),
        "ntp": find_column(hmap, ["NTP"]),
        "society": find_column(hmap, ["Society"]),
        "category_v2": find_column(hmap, ["Category V2"]),
    }

    records = []
    for r in range(header_row + 1, ws.max_row + 1):
        get = lambda c: ws.cell(r, c).value if c else None
        title = normalize_text(get(required["title"]))
        mms = normalize_mms(get(required["mms"]))
        bav = normalize_text(get(required["bav"]))
        language = normalize_text(get(required["language"]))
        if not title or title in {"--", "-", "N/A"}:
            continue
        if not (mms or bav):
            continue

        short_code_value = normalize_code(get(optional["short_code"]))
        if not short_code_value:
            short_code_value = short_code_from_bav(bav)

        records.append(
            {
                "mms_code": mms,
                "bav_code": bav,
                "short_code": short_code_value,
                "book_name": title,
                "lang_code": normalize_text(get(optional["lang_code"])),
                "language": language,
                "language_category": normalize_text(get(required["lang_category"])),
                "reprint_year": normalize_text(get(required["reprint"])),
                "edition_year": normalize_text(get(required["edition"])),
                "no_of_copies": normalize_text(get(optional["copies"])),
                "cost_price": to_number(get(optional["cost"])),
                "sale_price": to_number(get(optional["sale"])),
                "check_value": normalize_text(get(optional["check"])),
                "ver_max": normalize_text(get(optional["ver_max"])),
                "identity": normalize_text(get(optional["identity"])),
                "classification": normalize_text(get(optional["classification"])),
                "category": normalize_text(get(optional["category"])),
                "ntp": normalize_text(get(optional["ntp"])),
                "author": normalize_text(get(required["author"])),
                "society": normalize_text(get(optional["society"])),
                "category_v2": normalize_text(get(optional["category_v2"])),
            }
        )
    wb.close()
    if not records:
        raise ValueError("Books Master was parsed but no usable book records were found.")
    return records


def load_books_stock(path: Path) -> list[dict]:
    wb = load_workbook(path, read_only=False, data_only=True)
    ws = wb[wb.sheetnames[0]]

    # Existing MMS logic uses row 3 as the Books Stock header when possible.
    header_row = 3
    hmap = header_map(ws, header_row)
    mms_col = find_column(hmap, ["MMS Code", "MMS CODE"])
    if not mms_col:
        header_row = find_header_row(ws, [["MMS Code", "MMS CODE"]], 20)
        if header_row < 0:
            raise ValueError("Could not detect Books Stock Position MMS Code header.")
        hmap = header_map(ws, header_row)
        mms_col = find_column(hmap, ["MMS Code", "MMS CODE"])

    records = []
    for r in range(header_row + 1, ws.max_row + 1):
        mms = normalize_mms(ws.cell(r, mms_col).value)
        if not mms or "#REF!" in mms.upper():
            continue

        # Business rule from the established MMS builder:
        # Excel V = Total HQ DL Stock
        # Excel AM = PB HQ + Dera Stalls
        total_hq_dl = to_number(ws.cell(r, 22).value)  # V
        pb_hq_dera = to_number(ws.cell(r, 39).value)   # AM

        raw = {}
        for c in range(1, min(ws.max_column, 50) + 1):
            header = normalize_text(ws.cell(header_row, c).value) or f"COL_{c}"
            raw[header] = ws.cell(r, c).value

        records.append(
            {
                "mms_code": mms,
                "total_hq_dl_stock": total_hq_dl,
                "pb_hq_dera_stalls": pb_hq_dera,
                "raw_json": json.dumps(raw, default=str),
            }
        )
    wb.close()
    if not records:
        raise ValueError("Books Stock Position contains no usable stock rows.")
    return records


def find_sheet(wb, exact: str, contains: str | None = None):
    exact_norm = normalize_header(exact)
    for name in wb.sheetnames:
        if normalize_header(name) == exact_norm:
            return wb[name]
    if contains:
        contains_norm = normalize_header(contains)
        for name in wb.sheetnames:
            if contains_norm in normalize_header(name):
                return wb[name]
    return None


def load_audio_master(path: Path) -> list[dict]:
    wb = load_workbook(path, read_only=False, data_only=True)
    ws = find_sheet(wb, "Audio Master", "AUDIO")
    if ws is None:
        raise ValueError('Could not find sheet "Audio Master".')

    header_row = find_header_row(
        ws,
        [["Item Number (MMS)"], ["Item Details"], ["Old/Latest"], ["Type"]],
        30,
    )
    if header_row < 0:
        raise ValueError("Could not detect Audio Master header row.")
    hmap = header_map(ws, header_row)

    cols = {
        "mms": find_column(hmap, ["Item Number (MMS)"]),
        "bav": find_column(hmap, ["BAV code", "BAV Code"]),
        "title": find_column(hmap, ["Item Details"]),
        "old_latest": find_column(hmap, ["Old/Latest"]),
        "type": find_column(hmap, ["Type"]),
        "type_description": find_column(hmap, ["Type Description"]),
        "short": find_column(hmap, ["Short Code"]),
        "cost": find_column(hmap, ["Cost Price (Rs.)", "Cost Price(Rs.)"]),
        "sale": find_column(hmap, ["Sale Price (Rs.)", "Sale Price(Rs.)"]),
    }

    records = []
    for r in range(header_row + 1, ws.max_row + 1):
        get = lambda c: ws.cell(r, c).value if c else None
        if normalize_header(get(cols["old_latest"])) != "LATEST":
            continue
        title = normalize_text(get(cols["title"]))
        if not title:
            continue
        bav = normalize_text(get(cols["bav"]))
        short = normalize_code(get(cols["short"])) or short_code_from_bav(bav)
        records.append(
            {
                "mms_code": normalize_mms(get(cols["mms"])),
                "bav_code": bav,
                "short_code": short,
                "item_details": title,
                "old_latest": "Latest",
                "type": normalize_text(get(cols["type"])),
                "type_description": normalize_text(get(cols["type_description"])),
                "cost_price": to_number(get(cols["cost"])),
                "sale_price": to_number(get(cols["sale"])),
            }
        )
    wb.close()
    return records


def validate_other_workbook(source_type: str, path: Path) -> int:
    wb = load_workbook(path, read_only=False, data_only=True)
    count = 0

    if source_type == "SOSRC":
        ws = find_sheet(wb, "SOSRC Book List", "SOSRC")
        if ws is None:
            raise ValueError('Could not find "SOSRC Book List" sheet.')
        count = max(0, ws.max_row - 1)
    elif source_type == "PREVIOUS_INDENT":
        required_tabs = ["BOOKS", "AUDIO"]
        present = {normalize_header(n) for n in wb.sheetnames}
        missing = [name for name in required_tabs if name not in present]
        if missing:
            raise ValueError("Previous Indent is missing required sheet(s): " + ", ".join(missing))
        count = sum(wb[name].max_row for name in wb.sheetnames if normalize_header(name) in {"BOOKS", "AUDIO", "PHOTOS", "CALENDAR"})
    else:
        ws = wb[wb.sheetnames[0]]
        count = max(0, ws.max_row - 1)
    wb.close()
    return count


def parse_source_file(source_type: str, path: Path) -> list[dict] | int:
    if source_type == "BOOK_MASTER":
        return load_book_master(path)
    if source_type == "BOOK_STOCK":
        return load_books_stock(path)
    if source_type == "AUDIO_MASTER":
        return load_audio_master(path)
    return validate_other_workbook(source_type, path)


def replace_canonical_data(source_type: str, parsed: list[dict] | int, source_version_id: int) -> int:
    if isinstance(parsed, int):
        return parsed

    with db() as conn:
        if source_type == "BOOK_MASTER":
            conn.execute("DELETE FROM books_master")
            conn.executemany(
                """
                INSERT INTO books_master(
                    mms_code,bav_code,short_code,book_name,lang_code,language,language_category,
                    reprint_year,edition_year,no_of_copies,cost_price,sale_price,check_value,ver_max,
                    identity,classification,category,ntp,author,society,category_v2,source_version_id
                ) VALUES(
                    :mms_code,:bav_code,:short_code,:book_name,:lang_code,:language,:language_category,
                    :reprint_year,:edition_year,:no_of_copies,:cost_price,:sale_price,:check_value,:ver_max,
                    :identity,:classification,:category,:ntp,:author,:society,:category_v2,:source_version_id
                )
                """,
                [{**row, "source_version_id": source_version_id} for row in parsed],
            )
        elif source_type == "BOOK_STOCK":
            conn.execute("DELETE FROM books_stock")
            conn.executemany(
                """
                INSERT INTO books_stock(
                    mms_code,total_hq_dl_stock,pb_hq_dera_stalls,raw_json,source_version_id
                ) VALUES(
                    :mms_code,:total_hq_dl_stock,:pb_hq_dera_stalls,:raw_json,:source_version_id
                )
                """,
                [{**row, "source_version_id": source_version_id} for row in parsed],
            )
        elif source_type == "AUDIO_MASTER":
            conn.execute("DELETE FROM audio_master")
            conn.executemany(
                """
                INSERT INTO audio_master(
                    mms_code,bav_code,short_code,item_details,old_latest,type,type_description,
                    cost_price,sale_price,source_version_id
                ) VALUES(
                    :mms_code,:bav_code,:short_code,:item_details,:old_latest,:type,:type_description,
                    :cost_price,:sale_price,:source_version_id
                )
                """,
                [{**row, "source_version_id": source_version_id} for row in parsed],
            )
    return len(parsed)



def generic_workbook_records(path: Path) -> list[dict]:
    wb=load_workbook(path,read_only=True,data_only=True)
    out=[]
    try:
        for ws in wb.worksheets:
            rows=list(ws.iter_rows(values_only=True))
            if not rows: continue
            header_idx=None
            for i,row in enumerate(rows[:30]):
                if sum(1 for v in row if normalize_text(v))>=2:
                    header_idx=i;break
            if header_idx is None: continue
            headers=[];seen={}
            for j,v in enumerate(rows[header_idx]):
                h=normalize_text(v) or f"Column {j+1}"
                seen[h]=seen.get(h,0)+1
                if seen[h]>1:h=f"{h} ({seen[h]})"
                headers.append(h)
            for row_no,row in enumerate(rows[header_idx+1:],start=header_idx+2):
                vals=list(row)+[None]*(len(headers)-len(row))
                rec={headers[j]: normalize_text(vals[j]) for j in range(len(headers))}
                if any(rec.values()):
                    rec["__sheet__"]=ws.title;rec["__row__"]=row_no;out.append(rec)
    finally: wb.close()
    return out

def records_for_comparison(source_type: str,path: Path,parsed=None) -> list[dict]:
    if parsed is None:
        try: parsed=parse_source_file(source_type,path)
        except Exception: parsed=None
    if isinstance(parsed,list):
        cleaned=[]
        for row in parsed:
            cleaned.append({k:(json.dumps(v,sort_keys=True,ensure_ascii=False) if isinstance(v,(dict,list)) else normalize_text(v)) for k,v in row.items() if k not in {"source_version_id"}})
        return cleaned
    return generic_workbook_records(path)

def comparison_key(source_type: str,row: dict,index: int) -> str:
    candidates={
        "BOOK_MASTER":["bav_code","BAV Code","mms_code","MMS Code"],
        "BOOK_STOCK":["mms_code","MMS Code"],
        "AUDIO_MASTER":["mms_code","Item Number (MMS)","bav_code"],
        "AUDIO_STOCK":["mms_code","MMS Code"],
        "PHOTO_MASTER":["mms_code","Item Number (MMS)"],
        "PHOTO_STOCK":["mms_code","MMS Code"],
        "SOSRC":["MMS Code","BAV Code","Identity","Code"],
        "PREVIOUS_INDENT":["MMS CODE","MMS Code","CODE","Code"],
    }.get(source_type,[])
    for c in candidates:
        if normalize_text(row.get(c)):
            return normalize_code(row.get(c))
    # Generic workbook fallback: first meaningful non-metadata value plus sheet.
    vals=[normalize_text(v) for k,v in row.items() if not k.startswith('__') and normalize_text(v)]
    return f"{row.get('__sheet__','')}|{vals[0] if vals else index}"

def compare_source_versions(source_type: str,old_path: Path|None,new_path: Path,new_parsed=None) -> dict:
    old=[]
    if old_path and old_path.exists():
        try: old=records_for_comparison(source_type,old_path)
        except Exception: old=[]
    new=records_for_comparison(source_type,new_path,new_parsed)
    def index_rows(rows):
        d={}
        for i,r in enumerate(rows):
            key=comparison_key(source_type,r,i)
            # Preserve duplicates by suffix instead of silently discarding them.
            base=key;n=2
            while key in d: key=f"{base} #{n}";n+=1
            d[key]=r
        return d
    om=index_rows(old);nm=index_rows(new)
    added=[];removed=[];changed=[]
    for key in sorted(nm.keys()-om.keys()): added.append({"key":key,"new":nm[key]})
    for key in sorted(om.keys()-nm.keys()): removed.append({"key":key,"old":om[key]})
    for key in sorted(om.keys()&nm.keys()):
        o=om[key];n=nm[key];cols=[]
        for col in sorted(set(o)|set(n)):
            if col.startswith('__'): continue
            ov=normalize_text(o.get(col));nv=normalize_text(n.get(col))
            if ov!=nv: cols.append({"column":col,"old":ov,"new":nv})
        if cols: changed.append({"key":key,"columns":cols})
    return {"old_count":len(old),"new_count":len(new),"added_count":len(added),"removed_count":len(removed),"changed_count":len(changed),"added":added,"removed":removed,"changed":changed}

def diff_summary(diff: dict) -> str:
    return f"{diff.get('added_count',0)} added, {diff.get('removed_count',0)} removed, {diff.get('changed_count',0)} changed"

def stage_source_version(source_type: str,path: Path,name: str,drive_file_id=None,drive_modified_time=None,status='PENDING_REVIEW') -> dict:
    parsed=parse_source_file(source_type,path)
    src=source_row(source_type)
    active_path=Path(src['active_local_path']) if src.get('active_local_path') else None
    diff=compare_source_versions(source_type,active_path,path,parsed)
    stamp=now_iso()
    with db() as conn:
        conn.execute("UPDATE source_versions SET status='SUPERSEDED_PENDING',reviewed_at=?,decision='SUPERSEDED' WHERE source_type=? AND status='PENDING_REVIEW'",(stamp,source_type))
        cur=conn.execute("""INSERT INTO source_versions(source_type,drive_file_id,drive_file_name,drive_modified_time,synced_at,status,record_count,local_path,error_message,diff_json,previous_version_id) VALUES(?,?,?,?,?,?,?,?,NULL,?,?)""",(source_type,drive_file_id,name,drive_modified_time,stamp,status,int(diff['new_count']),str(path),json.dumps(diff,default=str),src.get('active_version_id')))
        version_id=cur.lastrowid
        conn.execute("UPDATE sources SET last_sync_attempt=?,last_status='PENDING_REVIEW',last_error=NULL WHERE source_type=?",(stamp,source_type))
    return {"ok":True,"pending_review":True,"version_id":version_id,"record_count":diff['new_count'],"diff":diff,"message":f"Verified new version: {diff_summary(diff)}. Review and accept or reject it."}

def activate_source_version(version_id: int) -> dict:
    with db() as conn:
        row=conn.execute("SELECT * FROM source_versions WHERE id=?",(version_id,)).fetchone()
    if not row: raise ValueError('Source version not found.')
    v=dict(row)
    if v['status']!='PENDING_REVIEW': raise ValueError('This version is no longer pending review.')
    path=Path(v['local_path'])
    if not path.exists(): raise ValueError('Pending source file is missing.')
    parsed=parse_source_file(v['source_type'],path)
    count=replace_canonical_data(v['source_type'],parsed,version_id)
    stamp=now_iso()
    with db() as conn:
        conn.execute("UPDATE source_versions SET status='ACTIVE',record_count=?,reviewed_at=?,decision='ACCEPTED' WHERE id=?",(count,stamp,version_id))
        conn.execute("UPDATE source_versions SET status='SUPERSEDED' WHERE source_type=? AND id<>? AND status='ACTIVE'",(v['source_type'],version_id))
        conn.execute("""UPDATE sources SET drive_file_name=?,drive_modified_time=?,active_local_path=?,active_version_id=?,record_count=?,last_successful_sync=?,last_status='ACTIVE',last_error=NULL WHERE source_type=?""",(v.get('drive_file_name'),v.get('drive_modified_time'),str(path),version_id,count,stamp,v['source_type']))
    return {"ok":True,"message":f"Accepted new {SOURCE_TYPES[v['source_type']]['label']} version with {count} records."}

def reject_source_version(version_id: int) -> dict:
    stamp=now_iso()
    with db() as conn:
        row=conn.execute("SELECT * FROM source_versions WHERE id=?",(version_id,)).fetchone()
        if not row: raise ValueError('Source version not found.')
        if row['status']!='PENDING_REVIEW': raise ValueError('This version is no longer pending review.')
        conn.execute("UPDATE source_versions SET status='REJECTED_BY_USER',reviewed_at=?,decision='REJECTED' WHERE id=?",(stamp,version_id))
        src=conn.execute("SELECT active_version_id FROM sources WHERE source_type=?",(row['source_type'],)).fetchone()
        new_status='ACTIVE' if src and src['active_version_id'] else 'NOT_CONFIGURED'
        conn.execute("UPDATE sources SET last_status=? WHERE source_type=?",(new_status,row['source_type']))
    return {"ok":True,"message":"Rejected the pending version. The previous active version was kept."}

# ----------------------------- syncing -----------------------------

_sync_lock = threading.Lock()


def configure_source(source_type: str, drive_file_id: str) -> None:
    if source_type not in SOURCE_TYPES:
        raise ValueError("Unknown source type.")
    with db() as conn:
        conn.execute(
            """
            UPDATE sources
            SET drive_file_id=?, last_status='CONFIGURED', last_error=NULL
            WHERE source_type=?
            """,
            (drive_file_id.strip(), source_type),
        )


def sync_source(source_type: str, force: bool = False) -> dict:
    if source_type not in SOURCE_TYPES: raise ValueError("Unknown source type.")
    with _sync_lock:
        src=source_row(source_type);file_id=normalize_text(src.get("drive_file_id"));attempt=now_iso()
        if not file_id:return {"ok":False,"source_type":source_type,"message":"Drive file ID is not configured."}
        with db() as conn: conn.execute("UPDATE sources SET last_sync_attempt=? WHERE source_type=?",(attempt,source_type))
        try:
            metadata=drive_metadata(file_id);modified=metadata.get("modifiedTime");name=metadata.get("name",SOURCE_TYPES[source_type]["label"])
            with db() as conn:
                pending=conn.execute("SELECT * FROM source_versions WHERE source_type=? AND status='PENDING_REVIEW' ORDER BY id DESC LIMIT 1",(source_type,)).fetchone()
            if pending and pending['drive_modified_time']==modified:
                return {"ok":True,"pending_review":True,"version_id":pending['id'],"message":"This Drive version is already waiting for review."}
            if src.get("drive_modified_time") and modified==src.get("drive_modified_time"):
                with db() as conn:
                    conn.execute("INSERT INTO source_versions(source_type,drive_file_id,drive_file_name,drive_modified_time,synced_at,status,record_count,local_path,error_message) VALUES(?,?,?,?,?,'NO_CHANGE',?,?,NULL)",(source_type,file_id,name,modified,attempt,int(src.get('record_count') or 0),src.get('active_local_path')))
                    conn.execute("UPDATE sources SET last_status='UP_TO_DATE',last_error=NULL WHERE source_type=?",(source_type,))
                return {"ok":True,"source_type":source_type,"message":"No Drive changes detected."}
            payload=drive_download(file_id,metadata.get("mimeType",""));path=SOURCE_DIR/f"{source_type.lower()}_{uuid.uuid4().hex}_pending.xlsx";path.write_bytes(payload)
            result=stage_source_version(source_type,path,name,file_id,modified);result['source_type']=source_type;return result
        except Exception as exc:
            error=str(exc)
            with db() as conn:
                conn.execute("INSERT INTO source_versions(source_type,drive_file_id,drive_file_name,drive_modified_time,synced_at,status,record_count,local_path,error_message) VALUES(?,?,?,?,?,'VALIDATION_FAILED',0,NULL,?)",(source_type,file_id,src.get('drive_file_name'),src.get('drive_modified_time'),attempt,error))
                conn.execute("UPDATE sources SET last_status='SYNC_FAILED',last_error=? WHERE source_type=?",(error,source_type))
            return {"ok":False,"source_type":source_type,"message":error,"kept_previous_valid_version":True}

def sync_all_sources(force: bool = False) -> list[dict]:
    results = []
    for source_type in SOURCE_TYPES:
        if normalize_text(source_row(source_type).get("drive_file_id")):
            results.append(sync_source(source_type, force=force))
    return results


def background_sync_loop():
    while True:
        time.sleep(SYNC_INTERVAL_SECONDS)
        try:
            sync_all_sources(force=False)
        except Exception as exc:
            print("Background sync error:", exc)





def import_local_test_source(source_type: str,payload: bytes,original_name: str) -> dict:
    if source_type not in SOURCE_TYPES: raise ValueError("Unknown source type.")
    if not payload: raise ValueError("No file was uploaded.")
    path=SOURCE_DIR/f"{source_type.lower()}_{uuid.uuid4().hex}_pending.xlsx";path.write_bytes(payload)
    try:
        result=stage_source_version(source_type,path,original_name,None,None);result['source_type']=source_type;return result
    except Exception as exc:
        if path.exists(): path.unlink()
        raise ValueError(f"{SOURCE_TYPES[source_type]['label']} could not be verified: {exc}") from exc


# ----------------------------- report queries -----------------------------

def query_books(market: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM books_master
            WHERE TRIM(COALESCE(book_name,'')) <> ''
            ORDER BY language, book_name
            """
        ).fetchall()
    books = [dict(r) for r in rows]
    return [b for b in books if market_allows(b.get("language_category", ""), market)]


def normalize_bav_search(value: Any) -> str:
    """Case-insensitive BAV matching that ignores hyphens, spaces and punctuation."""
    return re.sub(r"[^A-Z0-9]", "", normalize_text(value).upper())


def parse_bav_components(code: str) -> dict:
    """Parse BAV code such as EN-056-0-09-02 into identity / edition / impression."""
    parts=[p.strip() for p in normalize_text(code).split("-") if p.strip()!=""]
    result={"identity":"","edition_number":"","impression_number":""}
    if len(parts)>=5:
        result["identity"]=f"{parts[1]}-{parts[2]}"
        try: result["edition_number"]=str(int(parts[-2]))
        except Exception: result["edition_number"]=parts[-2]
        try: result["impression_number"]=str(int(parts[-1]))
        except Exception: result["impression_number"]=parts[-1]
    return result


def book_to_report_option(book: dict) -> dict:
    parsed=parse_bav_components(book.get("bav_code") or "")
    return {
        "book_id":book.get("id"),
        "code":book.get("bav_code") or "",
        "mms_code":book.get("mms_code") or "",
        "title":book.get("book_name") or "",
        "author":book.get("author") or "",
        "language":book.get("language") or "",
        "language_category":book.get("language_category") or "",
        "category":book.get("category") or "",
        "price":book.get("sale_price") or 0,
        "identity":book.get("identity") or parsed["identity"],
        "edition_number":parsed["edition_number"],
        "impression_number":parsed["impression_number"],
    }


def search_books_by_bav(query: str, market: str, limit: int=5000) -> list[dict]:
    """Search Book Master by BAV prefix, ignoring case and hyphens."""
    q=normalize_bav_search(query)
    if not q:
        return []

    with db() as conn:
        rows=conn.execute(
            "SELECT * FROM books_master ORDER BY UPPER(COALESCE(bav_code,'')), UPPER(COALESCE(book_name,''))"
        ).fetchall()

    out=[]
    for row in rows:
        book=dict(row)
        if not market_allows(book.get("language_category",""),market):
            continue
        if normalize_bav_search(book.get("bav_code")).startswith(q):
            out.append(book_to_report_option(book))
            if len(out)>=max(1,min(int(limit),5000)):
                break
    return out


def resolve_book_by_bav(code: str, market: str) -> dict | None:
    target=normalize_bav_search(code)
    if not target:
        return None
    with db() as conn:
        rows=conn.execute("SELECT * FROM books_master").fetchall()
    for row in rows:
        book=dict(row)
        if not market_allows(book.get("language_category",""),market):
            continue
        if normalize_bav_search(book.get("bav_code"))==target:
            return book_to_report_option(book)
    return None


def expected_out_of_stock_rows(market: str) -> list[dict]:
    """Reference list using the established MMS grey-row rule: V + AM < 10."""
    books=query_books(market)
    with db() as conn:
        stock_rows=conn.execute("SELECT * FROM books_stock").fetchall()
    stock_map={normalize_mms(r["mms_code"]):dict(r) for r in stock_rows}
    result=[]
    for book in books:
        stock=stock_map.get(normalize_mms(book.get("mms_code")))
        if not stock:
            continue
        dl=to_number(stock["total_hq_dl_stock"])
        pb=to_number(stock["pb_hq_dera_stalls"])
        combined=dl+pb
        if combined<10:
            result.append({
                "code":book.get("bav_code") or "",
                "mms_code":book.get("mms_code") or "",
                "title":book.get("book_name") or "",
                "language":book.get("language") or "",
                "language_category":book.get("language_category") or "",
                "dl_stock":dl,
                "pb_stock":pb,
                "combined_stock":combined,
                "reason":"Out of Stock because Total HQ DL Stock (Excel V) + PB HQ + Dera Stalls (Excel AM) is below 10.",
            })
    return result


def source_requirements_for_report(report_type: str) -> list[str]:
    if report_type in {"new-releases","book-catalog"}:
        return ["BOOK_MASTER"]
    if report_type=="out-of-stock":
        return ["BOOK_MASTER","BOOK_STOCK"]
    if report_type=="mms-indent":
        return ["BOOK_MASTER","BOOK_STOCK","SOSRC","PREVIOUS_INDENT","AUDIO_MASTER","AUDIO_STOCK"]
    return []


def missing_required_sources(report_type: str) -> list[str]:
    missing=[]
    for source_type in source_requirements_for_report(report_type):
        row=source_row(source_type)
        if not row.get("active_version_id"):
            missing.append(SOURCE_TYPES[source_type]["label"])
    return missing


def source_cards(report_type: str) -> list[dict]:
    types=source_requirements_for_report(report_type)
    versions=active_source_versions(types)
    return [versions[t] for t in types if t in versions]


def prune_empty_drafts(report_type: str | None = None) -> None:
    """Discard reports that were created but never received a row."""
    clauses = ["status='DRAFT'"]
    params = []
    if report_type:
        clauses.append("report_type=?")
        params.append(report_type)
    with db() as conn:
        rows = conn.execute(
            "SELECT id, draft_json FROM reports WHERE " + " AND ".join(clauses), params
        ).fetchall()
        empty_ids = []
        for row in rows:
            try:
                draft = json.loads(row["draft_json"] or "{}")
            except json.JSONDecodeError:
                draft = {}
            if not (draft.get("rows") or []):
                empty_ids.append((row["id"],))
        if empty_ids:
            conn.executemany("DELETE FROM reports WHERE id=?", empty_ids)


def recent_report_drafts(
    report_type: str | None = None,
    limit: int | None = 5,
    include_finalized: bool = False,
) -> list[dict]:
    prune_empty_drafts(report_type)
    clauses = ["status IN ('DRAFT','FINAL')" if include_finalized else "status='DRAFT'"]
    params = []
    if report_type:
        clauses.append("report_type=?")
        params.append(report_type)
    query = """
        SELECT id, report_type, market, month, year, title, status, created_at, updated_at,
               final_filename, output_file, draft_json
        FROM reports
        WHERE """ + " AND ".join(clauses) + """
        ORDER BY updated_at DESC, created_at DESC
    """
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    for r in rows:
        try:
            draft = json.loads(r.pop("draft_json") or "{}")
        except json.JSONDecodeError:
            draft = {}
        r["row_count"] = len(draft.get("rows") or [])
        r["label"] = REPORT_LABELS.get(r["report_type"], r["title"])
        r["file_name"] = r.get("final_filename") or (Path(r["output_file"]).name.split("_", 1)[-1] if r.get("output_file") else "")
    return rows


def home_payload() -> dict:
    sources = configured_sources()
    source_counts = {
        "total": len(sources),
        "ready": sum(1 for s in sources if s.get("active_version_id")),
        "needs_setup": sum(1 for s in sources if not s.get("active_version_id")),
        "pending": sum(1 for s in sources if s.get("last_status") == "PENDING_REVIEW"),
    }
    report_status = []
    for report_type, label in REPORT_LABELS.items():
        missing = missing_required_sources(report_type)
        report_status.append({
            "report_type": report_type,
            "label": label,
            "ready": not missing,
            "missing": missing,
        })
    return {
        "sources": sources,
        "labels": REPORT_LABELS,
        "source_counts": source_counts,
        "report_status": report_status,
        "recent_drafts": recent_report_drafts(limit=6),
    }


def create_report(report_type: str, market: str, month: int, year: int) -> dict:
    missing=missing_required_sources(report_type)
    if missing:
        raise ValueError("Missing synced source data: "+", ".join(missing))
    if report_type not in {"new-releases","book-catalog","out-of-stock"}:
        raise ValueError("This report type is generated by the existing MMS Indent Builder.")

    report_id=uuid.uuid4().hex
    title=REPORT_LABELS[report_type]
    source_versions=active_source_versions(source_requirements_for_report(report_type))
    payload={
        "rows":[],
        "meta":{
            "mode":"manual",
            "audit_events":[],
            "validation":None,
            "note":"This report is manually assembled. Book Master is used for code lookup and auto-fill only.",
        }
    }
    timestamp=now_iso()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO reports(
                id,report_type,market,month,year,title,status,created_at,updated_at,
                source_versions_json,original_json,draft_json,changes_json
            ) VALUES(?,?,?,?,?,?, 'DRAFT', ?, ?, ?, ?, ?, '[]')
            """,
            (
                report_id,report_type,market,month,year,title,timestamp,timestamp,
                json.dumps(source_versions,default=str),
                json.dumps(payload,default=str),
                json.dumps(payload,default=str),
            ),
        )
    return get_report(report_id)


def get_report(report_id: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        raise KeyError("Report not found.")
    result = dict(row)
    for field in ["source_versions_json", "original_json", "draft_json", "changes_json"]:
        result[field[:-5] if field.endswith("_json") else field] = json.loads(result[field] or "{}")
    return result


def diff_report(original: dict, draft: dict) -> list[dict]:
    changes = []
    original_rows = original.get("rows", [])
    draft_rows = draft.get("rows", [])

    original_by_id = {str(r.get("id")): r for r in original_rows}
    draft_by_id = {str(r.get("id")): r for r in draft_rows}

    for row_id, old in original_by_id.items():
        if row_id not in draft_by_id:
            changes.append({"type": "removed", "id": row_id, "title": old.get("title", "")})
            continue
        new = draft_by_id[row_id]
        for key in sorted(set(old) | set(new)):
            if key in {"image_data"}:
                if bool(old.get(key)) != bool(new.get(key)):
                    changes.append({"type": "field", "id": row_id, "field": "image", "from": "existing" if old.get(key) else "blank", "to": "uploaded" if new.get(key) else "blank"})
                continue
            if old.get(key) != new.get(key):
                changes.append(
                    {
                        "type": "field",
                        "id": row_id,
                        "title": new.get("title") or old.get("title") or "",
                        "field": key,
                        "from": old.get(key),
                        "to": new.get(key),
                    }
                )

    for row_id, new in draft_by_id.items():
        if row_id not in original_by_id:
            changes.append({"type": "added", "id": row_id, "title": new.get("title", "")})

    old_order = [str(r.get("id")) for r in original_rows if str(r.get("id")) in draft_by_id]
    new_order = [str(r.get("id")) for r in draft_rows if str(r.get("id")) in original_by_id]
    if old_order != new_order:
        changes.append({"type": "reordered", "message": "Report row order was changed."})

    return changes


def save_report_draft(
    report_id: str,
    draft: dict,
    return_report: bool=True,
    market: str | None=None,
    month: int | None=None,
    year: int | None=None,
):
    """Fast draft persistence. Structural diffing is deferred until finalization."""
    updates=["draft_json=?","updated_at=?"]
    params=[json.dumps(draft, default=str),now_iso()]
    if market in {"INDIAN","OVERSEAS"}:
        updates.append("market=?")
        params.append(market)
    if month is not None:
        updates.append("month=?")
        params.append(month)
    if year is not None:
        updates.append("year=?")
        params.append(year)
    params.append(report_id)
    with db() as conn:
        conn.execute(
            "UPDATE reports SET "+", ".join(updates)+" WHERE id=?",
            params,
        )
    return get_report(report_id) if return_report else None


def normalize_new_release_years(draft: dict) -> dict:
    """Keep editable edition and impression years as four-digit integers."""
    for row in draft.get("rows", []) or []:
        for key in ("edition_year", "impression_year"):
            row[key] = re.sub(r"\D", "", normalize_text(row.get(key)))[:4]
    return draft


# ----------------------------- PDF generation -----------------------------
def humanize_report_changes(changes: list[dict]) -> list[str]:
    out=[]
    for c in changes or []:
        typ=c.get('type','change')
        title=c.get('title') or c.get('code') or 'a book'
        if typ=='added':out.append(f"Added {title}.")
        elif typ=='removed':out.append(f"Removed {title}.")
        elif typ=='reordered':out.append("Changed the book order.")
        elif typ=='changed':out.append(f"Updated {title}.")
        else:out.append(f"{typ.replace('_',' ').title()}: {title}.")
    return out



def pdf_metadata_callback(title: str, author: str="MMS Status Circular"):
    """Set PDF metadata so browser PDF viewers do not display '(anonymous)'."""
    def _apply(canvas, doc):
        canvas.setTitle(title or "MMS Status Circular")
        canvas.setAuthor(author)
        canvas.setSubject("MMS Status Circular")
        canvas.setCreator("MMS Status Circular")
    return _apply



def sanitize_rich_text(value: Any) -> str:
    """Keep only simple formatting supported by the catalog description."""
    text="" if value is None else str(value)
    text=re.sub(r"(?i)<\s*(div|p)\b[^>]*>", "", text)
    text=re.sub(r"(?i)</\s*(div|p)\s*>", "<br/>", text)
    text=re.sub(r"(?i)<\s*br\s*/?\s*>", "<br/>", text)
    # Normalize semantic tags.
    text=re.sub(r"(?i)<\s*strong\s*>", "<b>", text)
    text=re.sub(r"(?i)</\s*strong\s*>", "</b>", text)
    text=re.sub(r"(?i)<\s*em\s*>", "<i>", text)
    text=re.sub(r"(?i)</\s*em\s*>", "</i>", text)
    # Remove every tag except b/i/u/br.
    text=re.sub(r"(?is)<(?!/?(?:b|i|u)\b|br\s*/?>)[^>]+>", "", text)
    return text.strip()



class RupeePrice(Flowable):
    """Font-independent rupee mark plus amount, so ₹ never becomes an empty square."""
    def __init__(self, amount, font_size=10.2, width=30 * mm):
        Flowable.__init__(self)
        self.amount = to_number(amount)
        self.font_size = font_size
        self.width = width
        self.height = font_size + 5

    def draw(self):
        c = self.canv
        fs = self.font_size
        amount_text=f"{self.amount:,.2f}"
        amount_width=pdfmetrics.stringWidth(amount_text,"Times-Roman",fs)
        symbol_width=fs*0.8
        total_width=symbol_width+amount_width
        x=max(0,(self.width-total_width)/2)
        y = 1.5

        # Vector rupee mark: vertical stem + curved upper bowl + two horizontal bars.
        c.setLineWidth(max(0.8, fs * 0.075))
        c.line(x + 1.2, y + fs * 0.05, x + 1.2, y + fs * 0.86)
        c.line(x + 1.2, y + fs * 0.86, x + fs * 0.48, y + fs * 0.86)
        c.line(x + 1.2, y + fs * 0.66, x + fs * 0.53, y + fs * 0.66)
        c.line(x + 0.6, y + fs * 0.51, x + fs * 0.53, y + fs * 0.51)
        c.line(x + fs * 0.48, y + fs * 0.86, x + fs * 0.62, y + fs * 0.73)
        c.line(x + fs * 0.62, y + fs * 0.73, x + fs * 0.48, y + fs * 0.58)
        c.line(x + fs * 0.47, y + fs * 0.58, x + 1.2, y + fs * 0.58)
        c.line(x + fs * 0.25, y + fs * 0.57, x + fs * 0.62, y + fs * 0.05)

        c.setFont("Times-Roman", fs)
        c.drawString(x + fs * 0.82, y, amount_text)



PDF_UNICODE_FONT = "Helvetica"
for _font_path in [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]:
    try:
        if Path(_font_path).exists():
            pdfmetrics.registerFont(TTFont("MMSUnicode", _font_path))
            PDF_UNICODE_FONT = "MMSUnicode"
            break
    except Exception:
        pass

def ordinal(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return "_"
    try:
        n = int(float(text))
    except Exception:
        return text
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1:"st",2:"nd",3:"rd"}.get(n % 10,"th")
    return f"{n}{suffix}"

def rupee_paragraph(amount: Any, style):
    return Paragraph(f'<font name="{PDF_UNICODE_FONT}">₹</font> {to_number(amount):,.2f}', style)

def classic_month_style():
    return ParagraphStyle("ClassicMonth", parent=styles["BodyText"],
        fontName="Times-Italic", fontSize=11.5, leading=13, alignment=TA_RIGHT)



styles = getSampleStyleSheet()
STYLE_TITLE = ParagraphStyle(
    "ReportTitle", parent=styles["Heading1"], fontName="Helvetica-Bold",
    fontSize=18, leading=21, alignment=TA_CENTER, spaceAfter=5
)
STYLE_SMALL = ParagraphStyle(
    "Small", parent=styles["BodyText"], fontSize=8.5, leading=10.5
)
STYLE_BODY = ParagraphStyle(
    "Body", parent=styles["BodyText"], fontSize=10, leading=12
)
STYLE_LANG = ParagraphStyle(
    "Lang", parent=styles["Heading3"], fontName="Helvetica-Bold",
    fontSize=10, leading=11
)


def month_name(month: int) -> str:
    return datetime(2000, month, 1).strftime("%B")


def report_header(title: str, month: int, year: int):
    return Table(
        [[
            Paragraph(f"<b>{title.upper()}</b>", STYLE_TITLE),
            Paragraph(f"<i>{month_name(month)}, {year}</i>", ParagraphStyle(
                "Month", parent=STYLE_BODY, alignment=TA_RIGHT, fontSize=10
            )),
        ]],
        colWidths=[145*mm, 40*mm],
        style=TableStyle([
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("LINEBELOW", (0,0), (-1,-1), 0.6, colors.grey),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ]),
    )


def generate_new_releases_pdf(report: dict, output_path: Path):
    """Portrait A4 New Releases circular matching the approved five-column template."""
    payload=report["draft"]
    rows=[r for r in payload.get("rows",[]) if r.get("included",True)]

    page_w,_=A4
    left=5.5*mm; right=5.5*mm
    usable_w=page_w-left-right
    doc=SimpleDocTemplate(
        str(output_path),pagesize=A4,
        leftMargin=left,rightMargin=right,topMargin=7*mm,bottomMargin=7*mm
    )

    title_style=ParagraphStyle("NRTemplateTitle",parent=styles["Heading1"],fontName="Times-Bold",fontSize=15.5,leading=17.5,alignment=TA_CENTER)
    month_style=ParagraphStyle("NRTemplateMonth",parent=styles["BodyText"],fontName="Times-Italic",fontSize=11.5,leading=13,alignment=TA_RIGHT)
    header_style=ParagraphStyle("NRTemplateHeader",parent=styles["BodyText"],fontName="Times-Bold",fontSize=11.8,leading=13.4,alignment=TA_CENTER)
    body_style=ParagraphStyle("NRTemplateBody",parent=styles["BodyText"],fontName="Times-Roman",fontSize=10.5,leading=12.4,alignment=TA_LEFT)
    lang_style=ParagraphStyle("NRTemplateLanguage",parent=styles["BodyText"],fontName="Times-Bold",fontSize=10.9,leading=12.5,alignment=TA_LEFT)
    edition_style=ParagraphStyle("NRTemplateEdition",parent=body_style,alignment=TA_CENTER)
    price_style=ParagraphStyle("NRTemplatePrice",parent=body_style,alignment=TA_CENTER)
    type_style=ParagraphStyle("NRTemplateType",parent=body_style,fontName="Times-Italic",textColor=colors.blue,alignment=TA_CENTER)

    widths=[29*mm,55*mm,22*mm,47*mm,28*mm,18*mm]
    top=Table(
        [["",Paragraph("<u>NEW RELEASES</u>",title_style),Paragraph(f"<i>{month_name(report['month'])}, {report['year']}</i>",month_style)]],
        colWidths=[usable_w*.22,usable_w*.56,usable_w*.22],hAlign="CENTER"
    )
    top.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"BOTTOM"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))

    data=[[
        Paragraph("<u>CODE</u>",header_style),
        Paragraph("<u>TITLE</u>",header_style),
        Paragraph("<u>TYPE</u>",header_style),
        Paragraph("<u>AUTHOR</u>",header_style),
        Paragraph("<u>EDN. YR</u><br/><u>IMP. YR</u>",header_style),
        Paragraph("<u>PRICE</u>",header_style),
    ]]
    language_rows=[]
    spacer_rows=[]
    grouped=[]
    current_lang=None
    current_group=[]
    for row in rows:
        language=normalize_header(row.get("language")) or "OTHER"
        if current_lang is not None and language!=current_lang:
            grouped.append((current_lang,current_group))
            current_group=[]
        current_lang=language
        current_group.append(row)
    if current_lang is not None:
        grouped.append((current_lang,current_group))

    for group_index,(language,group_rows) in enumerate(grouped):
        language_rows.append(len(data))
        data.append([Paragraph(f"<u>{xml_escape(language)}</u>",lang_style),"","","","",""])
        for row in group_rows:
            title=xml_escape(normalize_text(row.get("title")))
            book_type=xml_escape(normalize_text(row.get("type")))
            ed_year=xml_escape(re.sub(r"\D", "", normalize_text(row.get("edition_year")))[:4] or "_")
            imp_year=xml_escape(re.sub(r"\D", "", normalize_text(row.get("impression_year")))[:4] or "_")
            data.append([
                Paragraph(xml_escape(normalize_text(row.get("code"))),body_style),
                Paragraph(title,body_style),
                Paragraph(book_type,type_style),
                Paragraph(xml_escape(normalize_text(row.get("author"))),body_style),
                Paragraph(f"{ordinal(row.get('edition_number'))} Ed. {ed_year}<br/>{ordinal(row.get('impression_number'))} Imp. {imp_year}",edition_style),
                Paragraph(f"Rs. {to_number(row.get('price')):,.2f}",price_style),
            ])
        if group_index<len(grouped)-1:
            spacer_rows.append(len(data))
            data.append([Spacer(1,8.5),"","","","",""])

    table=Table(data,colWidths=widths,repeatRows=1,hAlign="CENTER")
    ts=[
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#C6C6C6")),
        ("BOX",(0,0),(-1,-1),1.0,colors.black),("INNERGRID",(0,0),(-1,-1),0.35,colors.HexColor("#707070")),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),2.0),("RIGHTPADDING",(0,0),(-1,-1),2.0),
        ("TOPPADDING",(0,0),(-1,0),7.5),("BOTTOMPADDING",(0,0),(-1,0),5.5),
        ("TOPPADDING",(0,1),(-1,-1),3.3),("BOTTOMPADDING",(0,1),(-1,-1),3.3),
    ]
    for row_index in language_rows:
        ts += [("TOPPADDING",(0,row_index),(-1,row_index),4.2),("BOTTOMPADDING",(0,row_index),(-1,row_index),4.2)]
    for row_index in spacer_rows:
        ts += [("TOPPADDING",(0,row_index),(-1,row_index),0),("BOTTOMPADDING",(0,row_index),(-1,row_index),0)]
    table.setStyle(TableStyle(ts))

    cb=pdf_metadata_callback(output_path.stem)
    doc.build([top,Spacer(1,3.2*mm),table],onFirstPage=cb,onLaterPages=cb)


def generate_out_of_stock_pdf(report: dict, output_path: Path):
    """Portrait A4 Out-of-Stock PDF with a balanced shared three-column grid."""
    payload=report["draft"]
    rows=[r for r in payload.get("rows",[]) if r.get("included",True)]

    page_w,page_h=A4
    left=7*mm; right=7*mm
    usable_w=page_w-left-right
    doc=SimpleDocTemplate(str(output_path),pagesize=A4,leftMargin=left,rightMargin=right,topMargin=7*mm,bottomMargin=7*mm)

    title_style=ParagraphStyle("OOSTitleV4",parent=styles["Heading1"],fontName="Times-Bold",fontSize=15.5,leading=17,alignment=TA_CENTER)
    month_style=ParagraphStyle("OOSMonthV4",parent=styles["BodyText"],fontName="Times-Italic",fontSize=10.3,leading=11.5,alignment=TA_RIGHT)
    lang_style=ParagraphStyle("OOSLangV5",parent=styles["BodyText"],fontName="Times-Bold",fontSize=11.2,leading=12.8,alignment=TA_LEFT)
    item_style=ParagraphStyle("OOSItemV5",parent=styles["BodyText"],fontName="Times-Roman",fontSize=10.2,leading=12.1,alignment=TA_LEFT)

    grouped={}
    languages=[]
    for row in rows:
        lang=normalize_header(row.get("language")) or "OTHER"
        if lang not in grouped:
            grouped[lang]=[]
            languages.append(lang)
        grouped[lang].append(normalize_text(row.get("title")))

    col_w=usable_w/3.0
    def units(text): return max(1,math.ceil(max(1,len(text))/29))
    cols=[[],[],[]]; loads=[0,0,0]
    for lang in languages:
        block=1.2+sum(units(t) for t in grouped[lang])
        target=min(range(3),key=lambda i:loads[i])
        cols[target].append((lang,grouped[lang]))
        loads[target]+=block

    # One outer table keeps every row line and blank cell aligned across all
    # three columns, unlike three independent nested tables.
    column_rows=[]
    for column in cols:
        values=[]
        for lang,titles in column:
            values.append((Paragraph(lang,lang_style),True))
            values.extend((Paragraph(title,item_style),False) for title in titles)
        column_rows.append(values)
    row_count=max(1,max(len(column) for column in column_rows))
    data=[]; header_cells=[]
    for row_index in range(row_count):
        rendered=[]
        for col_index,column in enumerate(column_rows):
            if row_index<len(column):
                value,is_header=column[row_index]
                rendered.append(value)
                if is_header:header_cells.append((col_index,row_index))
            else:
                rendered.append("")
        data.append(rendered)

    top=Table([["",Paragraph("<u>BOOKS OUT OF STOCK</u>",title_style),
                Paragraph(f"<i>{month_name(report['month'])}, {report['year']}</i>",month_style)]],
              colWidths=[usable_w*.22,usable_w*.56,usable_w*.22])
    top.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"BOTTOM"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    outer=Table(data,colWidths=[col_w]*3,hAlign="CENTER")
    table_style=[
        ("BOX",(0,0),(-1,-1),1.0,colors.black),("INNERGRID",(0,0),(-1,-1),0.3,colors.HexColor("#777777")),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),1.8),("RIGHTPADDING",(0,0),(-1,-1),1.8),
        ("TOPPADDING",(0,0),(-1,-1),2.2),("BOTTOMPADDING",(0,0),(-1,-1),2.2),
    ]
    for col_index,row_index in header_cells:
        table_style += [
            ("BACKGROUND",(col_index,row_index),(col_index,row_index),colors.HexColor("#D0D0D0")),
            ("LINEABOVE",(col_index,row_index),(col_index,row_index),0.8,colors.black),
            ("LINEBELOW",(col_index,row_index),(col_index,row_index),0.55,colors.black),
            ("TOPPADDING",(col_index,row_index),(col_index,row_index),3.0),
            ("BOTTOMPADDING",(col_index,row_index),(col_index,row_index),3.0),
        ]
    outer.setStyle(TableStyle(table_style))

    cb=pdf_metadata_callback(output_path.stem)
    doc.build([top,Spacer(1,3.2*mm),outer],onFirstPage=cb,onLaterPages=cb)


def decode_image_data(data_url: str) -> bytes | None:
    if not data_url or "," not in data_url:
        return None
    try:
        return base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return None


def generate_catalog_pdf(report: dict, output_path: Path):
    """Portrait A4 catalog with three equal book cards per page."""
    payload=report["draft"]
    rows=[r for r in payload.get("rows",[]) if r.get("included",True)]

    page_w,page_h=A4
    left=10*mm; right=10*mm; top_margin=12*mm; bottom=8*mm
    doc=SimpleDocTemplate(
        str(output_path),pagesize=A4,
        leftMargin=left,rightMargin=right,topMargin=top_margin,bottomMargin=bottom
    )
    usable_w=page_w-left-right

    title_style=ParagraphStyle(
        "CatTitle",parent=styles["Heading1"],
        fontName="Helvetica-Bold",fontSize=13.5,leading=15,alignment=TA_LEFT
    )
    month_style=ParagraphStyle(
        "CatMonth",parent=styles["BodyText"],
        fontName="Times-Italic",fontSize=10.7,leading=12,alignment=TA_RIGHT
    )
    book_style=ParagraphStyle(
        "CatBook",parent=styles["BodyText"],
        fontName="Times-Roman",fontSize=9.8,leading=11.1,alignment=TA_LEFT
    )
    category_style=ParagraphStyle(
        "CatCategory",parent=styles["BodyText"],
        fontName="Times-BoldItalic",fontSize=10.2,leading=11.5,alignment=TA_CENTER
    )
    code_style=ParagraphStyle(
        "CatCode",parent=styles["BodyText"],
        fontName="Times-Roman",fontSize=9.5,leading=10.8,alignment=TA_LEFT
    )

    def header():
        h=Table([[
            Paragraph("NEW BOOK  RELEASES",title_style),
            Paragraph(f"<i>{month_name(report['month'])}, {report['year']}</i>",month_style)
        ]],colWidths=[usable_w*.72,usable_w*.28])
        h.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LINEBELOW",(0,0),(-1,-1),0.65,colors.HexColor("#9B9B9B")),
            ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
            ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),3.5),
        ]))
        return h

    header_block=header()
    header_height=header_block.wrap(usable_w,page_h)[1]
    header_gap=7*mm
    story=[header_block,Spacer(1,header_gap)]

    # Portrait pages use taller covers so three cards fill the page evenly.
    image_w=50*mm
    image_h=70*mm
    text_w=usable_w-image_w-7*mm
    # SimpleDocTemplate reserves 6 pt above and below its content frame.
    # Account for that fixed padding so all three cards fit on every page.
    frame_padding=12
    block_h=(page_h-top_margin-bottom-header_height-header_gap-frame_padding)/3

    for idx,row in enumerate(rows):
        if idx and idx%3==0:
            story.append(PageBreak())
            story.extend([header(),Spacer(1,header_gap)])

        image_bytes=decode_image_data(row.get("image_data",""))
        if image_bytes:
            image=Image(io.BytesIO(image_bytes),width=image_w,height=image_h,kind="proportional")
        else:
            image=Table([[""]],colWidths=[image_w],rowHeights=[image_h],
                style=TableStyle([
                    ("BOX",(0,0),(-1,-1),0.4,colors.HexColor("#C8C8C8")),
                    ("BACKGROUND",(0,0),(-1,-1),colors.white),
                ]))

        language=normalize_text(row.get("language"))
        title=normalize_text(row.get("title"))
        author=normalize_text(row.get("author"))
        category=normalize_text(row.get("category"))
        description=sanitize_rich_text(row.get("description_html") or row.get("description")) or " "
        code=normalize_text(row.get("code"))
        description_font_size=max(8.0,min(14.0,to_number(row.get("description_font_size")) or 10.5))
        desc_style=ParagraphStyle(
            f"CatDesc{idx}",parent=styles["BodyText"],fontName="Times-Roman",
            fontSize=description_font_size,leading=description_font_size*1.22,alignment=TA_LEFT
        )

        top_text=Table([[
            Paragraph(f'{title} <b>({language.title() if language else ""})</b><br/><b>Author:</b>&nbsp;&nbsp;{author or "—"}',book_style),
            Paragraph(category or " ",category_style)
        ]],colWidths=[text_w*.62,text_w*.38])
        top_text.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
            ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0),
        ]))

        top_height=top_text.wrap(text_w,image_h)[1]
        code_line=Paragraph(f"<b>Code:</b>&nbsp; {code}",code_style)
        code_height=code_line.wrap(text_w,image_h)[1]
        description_gap=3*mm
        description_height=max(1,image_h-top_height-code_height-description_gap-2*mm)
        description_frame=KeepInFrame(
            text_w,description_height,[Paragraph(description,desc_style)],
            mode="truncate",hAlign="LEFT",vAlign="TOP"
        )

        # Keep every card symmetric: description text stays in its frame and
        # the BAV code always lands on the bottom-left line beside the cover.
        text_area=Table([
            [top_text],
            [Spacer(1,description_gap)],
            [description_frame],
            [code_line],
        ],colWidths=[text_w],rowHeights=[top_height,description_gap,description_height,code_height])
        text_area.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
            ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0),
            ("VALIGN",(0,3),(0,3),"BOTTOM"),
        ]))

        block=Table([[image,text_area]],colWidths=[image_w+6*mm,text_w],rowHeights=[block_h])
        block.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
            ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0),
        ]))
        story.append(block)

    cb=pdf_metadata_callback(output_path.stem)
    doc.build(story,onFirstPage=cb,onLaterPages=cb)


def sanitize_pdf_filename(requested: str, fallback: str) -> str:
    """Return a safe PDF filename chosen by the user."""
    name=normalize_text(requested) or normalize_text(fallback) or "report"
    if name.lower().endswith(".pdf"):
        name=name[:-4]
    name=re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    name=re.sub(r"\s+", " ", name).strip(" ._-")
    name=(name[:120].rstrip(" ._-") or "report")
    return name+".pdf"


def render_report_pdf(report: dict, output_path: Path) -> None:
    """Render one report without changing its draft or finalization status."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if report["report_type"] == "new-releases":
        generate_new_releases_pdf(report, output_path)
    elif report["report_type"] == "book-catalog":
        generate_catalog_pdf(report, output_path)
    elif report["report_type"] == "out-of-stock":
        generate_out_of_stock_pdf(report, output_path)
    else:
        raise ValueError("Unsupported PDF report type.")


def report_pdf_filename(report: dict, requested_filename: str = "") -> str:
    fallback = f"{report['title']} - {month_name(report['month'])} {report['year']}"
    return sanitize_pdf_filename(requested_filename, fallback)


def finalize_report(report_id: str, requested_filename: str = "") -> Path:
    report = get_report(report_id)
    if not (report.get("draft") or {}).get("rows"):
        raise ValueError("No books have been added to this report.")
    filename = report_pdf_filename(report, requested_filename)
    final_changes = diff_report(report["original"], report["draft"])
    output_path = REPORT_DIR / report_id / filename
    previous_output = Path(report.get("output_file") or "")
    render_report_pdf(report, output_path)

    with db() as conn:
        conn.execute(
            """
            UPDATE reports
            SET status='FINAL', finalized_at=?, updated_at=?, output_file=?, final_filename=?, changes_json=?
            WHERE id=?
            """,
            (now_iso(), now_iso(), str(output_path), filename, json.dumps(final_changes, default=str), report_id),
        )
    if previous_output != output_path and previous_output.exists() and REPORT_DIR in previous_output.parents:
        try:
            previous_output.unlink()
        except OSError:
            pass
    return output_path


def preview_report(report_id: str, requested_filename: str = "") -> tuple[Path, str]:
    report = get_report(report_id)
    if not (report.get("draft") or {}).get("rows"):
        raise ValueError("No books have been added to this report.")
    filename = report_pdf_filename(report, requested_filename)
    output_path = REPORT_DIR / "_previews" / report_id / filename
    render_report_pdf(report, output_path)
    return output_path, filename


# ----------------------------- routes -----------------------------

def database_source_payload(source_type):
    if source_type not in SOURCE_TYPES:
        raise KeyError("Unknown source type.")
    source=source_row(source_type)
    with db() as conn:
        history=[dict(r) for r in conn.execute("SELECT * FROM source_versions WHERE source_type=? ORDER BY id DESC LIMIT 100",(source_type,)).fetchall()]
        pending_row=conn.execute("SELECT * FROM source_versions WHERE source_type=? AND status='PENDING_REVIEW' ORDER BY id DESC LIMIT 1",(source_type,)).fetchone()
    for h in history:
        d=json.loads(h.get('diff_json') or '{}');h['summary']=diff_summary(d) if d else ''
    pending=None
    if pending_row:
        pending=dict(pending_row);pending['diff']=json.loads(pending.get('diff_json') or '{}')
        for x in pending['diff'].get('added',[]):x['new_pretty']=json.dumps(x.get('new',{}),indent=2,ensure_ascii=False)
        for x in pending['diff'].get('removed',[]):x['old_pretty']=json.dumps(x.get('old',{}),indent=2,ensure_ascii=False)
    return {"source_type":source_type,"source":source,"history":history,"pending":pending}


def history_payload():
    report_type=request.args.get("type","");status=request.args.get("status","").upper()
    clauses=[];params=[]
    if report_type in REPORT_LABELS:clauses.append("report_type=?");params.append(report_type)
    if status in {"DRAFT","FINAL"}:clauses.append("status=?");params.append(status)
    where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
    with db() as conn: rows=[dict(r) for r in conn.execute("SELECT * FROM reports"+where+" ORDER BY created_at DESC",params).fetchall()]
    for r in rows:r['file_name']=r.get('final_filename') or (Path(r['output_file']).name.split('_',1)[-1] if r.get('output_file') else '')
    return {"reports":rows,"selected_type":report_type if report_type in REPORT_LABELS else "","selected_status":status if status in {"DRAFT","FINAL"} else "","labels":REPORT_LABELS}


def history_detail_payload(report_id):
    report=get_report(report_id)
    report['file_name']=report.get('final_filename') or (Path(report['output_file']).name.split('_',1)[-1] if report.get('output_file') else '')
    report['audit_events']=(report.get('draft') or {}).get('meta',{}).get('audit_events',[])
    report['human_changes']=humanize_report_changes(report.get('changes',[]))
    attachments=json.loads(report.get('attachments_json') or '{}') if isinstance(report.get('attachments_json'),str) else (report.get('attachments_json') or {})
    report['attachment_links']={k:url_for('download_report_attachment',report_id=report_id,name=k) for k in attachments}
    return {"report":report}


@app.route("/")
def home():
    return serve_frontend_file("start.html")


@app.route("/start.html")
def frontend_start():
    return serve_frontend_file("start.html")


@app.route("/css/<path:filename>")
def frontend_css(filename):
    return send_from_directory(FRONTEND_DIR / "css", filename)


@app.route("/js/<path:filename>")
def frontend_js(filename):
    return send_from_directory(FRONTEND_DIR / "js", filename)


@app.route("/assets/<path:filename>")
def frontend_assets(filename):
    return send_from_directory(FRONTEND_DIR / "assets", filename)


@app.route("/api/frontend/report/<report_type>")
def report_page(report_type):
    if report_type not in {"new-releases", "book-catalog", "out-of-stock"}:
        return jsonify({"ok":False,"message":"Unknown report type."}),404
    return jsonify({
        "ok":True,
        "report_type":report_type,
        "report_label":REPORT_LABELS[report_type],
        "source_cards":source_cards(report_type),
        "missing":missing_required_sources(report_type),
        "recent_drafts":recent_report_drafts(report_type, limit=None, include_finalized=True),
    })


@app.route("/report/<report_type>")
@app.route("/<report_type>")
@app.route("/<report_type>/")
@app.route("/<report_type>/index.html")
def frontend_report_page(report_type):
    if report_type not in {"new-releases", "book-catalog", "out-of-stock"}:
        return "Not found", 404
    return serve_frontend_file(f"{report_type}/index.html")


@app.route("/api/frontend/database")
def database_page():
    return jsonify({"ok":True,"sources":configured_sources(),"labels":REPORT_LABELS})


@app.route("/api/frontend/home")
def frontend_home_data():
    return jsonify({"ok":True,**home_payload()})


@app.route("/database")
@app.route("/database/")
@app.route("/database/index.html")
def frontend_database_page():
    return serve_frontend_file("database/index.html")


@app.route("/database/<source_type>")
@app.route("/api/frontend/database/<source_type>")
def database_source_page(source_type):
    try:
        return jsonify({"ok":True,**database_source_payload(source_type)})
    except KeyError:
        return jsonify({"ok":False,"message":"Unknown source type."}),404


@app.route("/history")
@app.route("/history/")
@app.route("/history/index.html")
def frontend_history_page():
    return serve_frontend_file("history/index.html")


@app.route("/api/frontend/history")
def history_page():
    return jsonify({"ok":True,**history_payload()})


@app.route("/history/<report_id>")
@app.route("/api/frontend/history/<report_id>")
def report_history_detail(report_id):
    try:
        return jsonify({"ok":True,**history_detail_payload(report_id)})
    except KeyError:
        return jsonify({"ok":False,"message":"Report not found."}),404


@app.route("/mms-indent")
@app.route("/mms-indent/")
@app.route("/mms-indent/index.html")
def mms_indent():
    return serve_frontend_file("mms-indent/index.html")


@app.route("/api/source/version/<int:version_id>/accept",methods=["POST"])
def api_accept_source_version(version_id):
    try:return jsonify(activate_source_version(version_id))
    except Exception as exc:return jsonify({"ok":False,"message":str(exc)}),400

@app.route("/api/source/version/<int:version_id>/reject",methods=["POST"])
def api_reject_source_version(version_id):
    try:return jsonify(reject_source_version(version_id))
    except Exception as exc:return jsonify({"ok":False,"message":str(exc)}),400

def delete_report_record(report_id: str) -> bool:
    with db() as conn:
        row=conn.execute("SELECT output_file,attachments_json FROM reports WHERE id=?",(report_id,)).fetchone()
        if not row:return False
        paths=[]
        if row['output_file']:paths.append(row['output_file'])
        try:paths.extend(json.loads(row['attachments_json'] or '{}').values())
        except Exception:pass
        conn.execute("DELETE FROM reports WHERE id=?",(report_id,))
    for p in paths:
        try:
            pp=Path(p)
            if pp.exists() and REPORT_DIR in pp.parents:
                pp.unlink()
                if pp.parent != REPORT_DIR and pp.parent.parent == REPORT_DIR:
                    pp.parent.rmdir()
        except Exception:pass
    return True


@app.route("/api/report/<report_id>/delete",methods=["POST"])
def api_delete_report(report_id):
    if not delete_report_record(report_id):
        return jsonify({"ok":False,"message":"Report not found."}),404
    return jsonify({"ok":True,"message":"Report permanently deleted."})


@app.route("/api/reports/delete-bulk",methods=["POST"])
def api_delete_reports_bulk():
    data=request.get_json(silent=True) or {}
    report_ids=[str(report_id) for report_id in data.get("report_ids",[]) if str(report_id).strip()]
    if not report_ids:
        status=str(data.get("status","")).upper()
        report_type=str(data.get("report_type","")).strip()
        if status not in {"DRAFT","FINAL"}:
            return jsonify({"ok":False,"message":"Choose reports or a report status to delete."}),400
        clauses=["status=?"];params=[status]
        if report_type in REPORT_LABELS:
            clauses.append("report_type=?");params.append(report_type)
        with db() as conn:
            report_ids=[row["id"] for row in conn.execute("SELECT id FROM reports WHERE "+" AND ".join(clauses),params).fetchall()]
    deleted=sum(1 for report_id in report_ids if delete_report_record(report_id))
    return jsonify({"ok":True,"deleted":deleted,"message":f"Deleted {deleted} report(s)."})

@app.route("/api/source/configure", methods=["POST"])
def api_configure_source():
    data = request.get_json(force=True)
    try:
        configure_source(data["source_type"], data["drive_file_id"])
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.route("/api/source/sync/<source_type>", methods=["POST"])
def api_sync_source(source_type):
    if source_type not in SOURCE_TYPES:
        return jsonify({"ok": False, "message": "Unknown source type."}), 404
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force", True))
    result = sync_source(source_type, force=force)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/source/sync-all", methods=["POST"])
def api_sync_all():
    return jsonify({"ok": True, "results": sync_all_sources(force=True)})



@app.route("/api/source/upload/<source_type>", methods=["POST"])
def api_upload_source(source_type):
    if source_type not in SOURCE_TYPES:
        return jsonify({"ok": False, "message": "Unknown source type."}), 404
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "message": "Choose a workbook first."}), 400
    try:
        result = import_local_test_source(source_type, file.read(), file.filename or "local_test.xlsx")
        return jsonify(result)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "message": str(exc),
            "kept_previous_valid_version": True,
        }), 400


@app.route("/api/mms-indent/archive",methods=["POST"])
def api_archive_mms_indent():
    file=request.files.get('file');kind=request.form.get('kind','main');market=request.form.get('market','MMS');report_id=request.form.get('report_id','').strip()
    if not file:return jsonify({"ok":False,"message":"No indent file received."}),400
    safe=sanitize_pdf_filename(file.filename or 'MMS_Indent_Output.xlsx','MMS_Indent_Output.xlsx')
    if safe.lower().endswith('.pdf'):safe=safe[:-4]+'.xlsx'
    else:safe=re.sub(r'\.pdf$','.xlsx',safe,flags=re.I)
    if not safe.lower().endswith('.xlsx'):safe+= '.xlsx'
    target=REPORT_DIR/f"{uuid.uuid4().hex}_{safe}";file.save(target)
    now=now_iso()
    if not report_id:
        report_id=uuid.uuid4().hex
        sources=active_source_versions(source_requirements_for_report('mms-indent'))
        payload={"rows":[],"meta":{"audit_events":[{"type":"indent_archived","message":"MMS Indent file archived in report history.","time":now}]}}
        with db() as conn:conn.execute("""INSERT INTO reports(id,report_type,market,month,year,title,status,created_at,updated_at,finalized_at,source_versions_json,original_json,draft_json,changes_json,output_file,attachments_json,final_filename) VALUES(?,?,?,?,?,?,'FINAL',?,?,?,?,?,?,?,?,?,?)""",(report_id,'mms-indent',market,datetime.now().month,datetime.now().year,'MMS Indent',now,now,now,json.dumps(sources,default=str),json.dumps(payload),json.dumps(payload),'[]',str(target) if kind=='main' else None,json.dumps({kind:str(target)} if kind!='main' else {}),safe if kind=='main' else None))
    else:
        with db() as conn:
            row=conn.execute("SELECT attachments_json FROM reports WHERE id=?",(report_id,)).fetchone()
            if row:
                attachments=json.loads(row['attachments_json'] or '{}')
                if kind=='main':conn.execute("UPDATE reports SET output_file=?,final_filename=?,updated_at=? WHERE id=?",(str(target),safe,now,report_id))
                else:
                    attachments[kind]=str(target);conn.execute("UPDATE reports SET attachments_json=?,updated_at=? WHERE id=?",(json.dumps(attachments),now,report_id))
    return jsonify({"ok":True,"report_id":report_id})

@app.route("/report-attachment/<report_id>/<name>")
def download_report_attachment(report_id,name):
    with db() as conn:row=conn.execute("SELECT attachments_json FROM reports WHERE id=?",(report_id,)).fetchone()
    if not row:return "Not found",404
    try:path=Path(json.loads(row['attachments_json'] or '{}').get(name,''))
    except Exception:return "Not found",404
    if not path.exists():return "Not found",404
    return send_file(path,as_attachment=True,download_name=path.name.split('_',1)[-1])

@app.route("/api/status")
def api_status():
    return jsonify({"ok": True, "sources": configured_sources(), "time": now_iso()})



@app.route("/api/books/options")
def api_books_options():
    market=request.args.get("market","INDIAN")
    try:
        with db() as conn:
            rows=conn.execute(
                "SELECT * FROM books_master ORDER BY UPPER(COALESCE(bav_code,'')), UPPER(COALESCE(book_name,''))"
            ).fetchall()
        books=[]
        for row in rows:
            book=dict(row)
            if market_allows(book.get("language_category",""),market):
                books.append(book_to_report_option(book))
        return jsonify({"ok":True,"books":books})
    except Exception as exc:
        return jsonify({"ok":False,"message":str(exc)}),400


@app.route("/api/books/search")
def api_books_search():
    q=request.args.get("q","")
    market=request.args.get("market","INDIAN")
    try:
        return jsonify({"ok":True,"books":search_books_by_bav(q,market,5000)})
    except Exception as exc:
        return jsonify({"ok":False,"message":str(exc)}),400


@app.route("/api/books/resolve")
def api_books_resolve():
    code=request.args.get("code","")
    market=request.args.get("market","INDIAN")
    try:
        book=resolve_book_by_bav(code,market)
        return jsonify({"ok":True,"found":book is not None,"book":book})
    except Exception as exc:
        return jsonify({"ok":False,"message":str(exc)}),400


@app.route("/api/report/<report_id>/validate-out-of-stock", methods=["POST"])
def api_validate_out_of_stock(report_id):
    try:
        report=get_report(report_id)
        if report["report_type"]!="out-of-stock":
            raise ValueError("Validation is available only for Out of Stock Books.")

        draft=report["draft"]
        user_rows=draft.get("rows",[])
        expected=expected_out_of_stock_rows(report["market"])
        expected_map={normalize_code(x["code"]):x for x in expected if normalize_code(x["code"])}
        user_codes=[normalize_code(r.get("code")) for r in user_rows if normalize_code(r.get("code"))]
        user_set=set(user_codes)

        missing=[x for code,x in expected_map.items() if code not in user_set]
        extra=[]
        reviewed=[]

        with db() as conn:
            stock_rows=conn.execute("SELECT * FROM books_stock").fetchall()
        stock_map={normalize_mms(r["mms_code"]):dict(r) for r in stock_rows}

        for row in user_rows:
            code=normalize_code(row.get("code"))
            if not code:
                continue
            expected_row=expected_map.get(code)
            if expected_row:
                row["validation_data"]={
                    **expected_row,
                    "status":"Matches MMS out-of-stock logic"
                }
                reviewed.append({
                    "code":code,
                    "title":row.get("title") or expected_row.get("title") or "",
                    "language":row.get("language") or expected_row.get("language") or "Other",
                    "dl_stock":expected_row.get("dl_stock"),
                    "pb_stock":expected_row.get("pb_stock"),
                    "combined_stock":expected_row.get("combined_stock"),
                    "reason":"Matches current stock-data out-of-stock rule."
                })
                continue

            book=resolve_book_by_bav(code,report["market"])
            if not book:
                reason="Code is not in the current Book Master for this market. Review the code or update the source data if this is a valid new book."
                row["validation_data"]={"dl_stock":None,"pb_stock":None,"combined_stock":None,"reason":reason}
                item={"code":code,"title":row.get("title") or "","language":row.get("language") or "Other","dl_stock":None,"pb_stock":None,"combined_stock":None,"reason":reason}
                extra.append(item)
                reviewed.append(item)
                continue

            stock=stock_map.get(normalize_mms(book.get("mms_code")))
            if not stock:
                reason="Book exists in Book Master but has no matching Books Stock Position row, so the MMS out-of-stock rule cannot be verified. Review/update the stock source."
                row["validation_data"]={"dl_stock":None,"pb_stock":None,"combined_stock":None,"reason":reason}
                item={"code":code,"title":book.get("title") or "","language":book.get("language") or row.get("language") or "Other","dl_stock":None,"pb_stock":None,"combined_stock":None,"reason":reason}
                extra.append(item)
                reviewed.append(item)
                continue

            dl=to_number(stock["total_hq_dl_stock"])
            pb=to_number(stock["pb_hq_dera_stalls"])
            combined=dl+pb
            if combined>=10:
                reason=f"Current MMS logic would NOT mark this book Out of Stock because {dl:g} + {pb:g} = {combined:g}, which is not below 10."
            else:
                reason="Current stock values are below 10, so this book should be reviewed for inclusion. If it was not in the expected set, verify market/category/source synchronization."
            row["validation_data"]={"dl_stock":dl,"pb_stock":pb,"combined_stock":combined,"reason":reason}
            item={"code":code,"title":book.get("title") or "","language":book.get("language") or row.get("language") or "Other","dl_stock":dl,"pb_stock":pb,"combined_stock":combined,"reason":reason}
            extra.append(item)
            reviewed.append(item)

        validation={
            "validated_at":now_iso(),
            "user_count":len(user_set),
            "expected_count":len(expected_map),
            "missing":missing,
            "extra":extra,
            "reviewed":reviewed,
            "sources":source_cards("out-of-stock"),
            "logic":"Total HQ DL Stock (Excel V) + PB HQ + Dera Stalls (Excel AM) < 10",
        }
        draft.setdefault("meta",{})["validation"]=validation
        draft["meta"].setdefault("audit_events",[]).append({
            "type":"out_of_stock_validation",
            "message":f"Validation run: {len(missing)} missing and {len(extra)} extra/review item(s).",
            "time":now_iso(),
            "missing_codes":[x["code"] for x in missing],
            "extra_codes":[x["code"] for x in extra],
        })
        updated=save_report_draft(report_id,draft,return_report=True)
        return jsonify({"ok":True,"report":updated})
    except Exception as exc:
        return jsonify({"ok":False,"message":str(exc)}),400


@app.route("/api/report/generate", methods=["POST"])
def api_generate_report():
    data = request.get_json(force=True)
    try:
        report = create_report(
            data["report_type"],
            data.get("market", "INDIAN"),
            int(data.get("month", datetime.now().month)),
            int(data.get("year", datetime.now().year)),
        )
        return jsonify({"ok": True, "report": report})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.route("/api/report/<report_id>", methods=["GET", "PUT"])
def api_report(report_id):
    try:
        if request.method == "GET":
            return jsonify({"ok": True, "report": get_report(report_id)})
        report = get_report(report_id)
        payload = request.get_json(force=True)
        draft = payload.get("draft") if isinstance(payload,dict) and isinstance(payload.get("draft"),dict) else payload
        if not isinstance(draft,dict):
            raise ValueError("Report draft must be an object.")
        if report["report_type"] == "new-releases":
            draft = normalize_new_release_years(draft)
        market = payload.get("market") if isinstance(payload,dict) and "draft" in payload else None
        month = payload.get("month") if isinstance(payload,dict) and "draft" in payload else None
        year = payload.get("year") if isinstance(payload,dict) and "draft" in payload else None
        if month is not None:
            month=int(month)
            if month not in range(1,13):
                raise ValueError("Month must be between 1 and 12.")
        if year is not None:
            year=int(year)
            if year not in range(2000,2101):
                raise ValueError("Year must be between 2000 and 2100.")
        save_report_draft(report_id, draft, return_report=False, market=market, month=month, year=year)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.route("/api/report/<report_id>/add-book-options")
def api_add_book_options(report_id):
    try:
        report = get_report(report_id)
        books = query_books(report["market"])
        return jsonify({
            "ok": True,
            "books": [
                {
                    "id": f"added-{b['id']}-{uuid.uuid4().hex[:5]}",
                    "book_id": b["id"],
                    "code": b.get("bav_code") or "",
                    "mms_code": b.get("mms_code") or "",
                    "title": b.get("book_name") or "",
                    "author": b.get("author") or "",
                    "language": b.get("language") or "",
                    "language_category": b.get("language_category") or "",
                    "edition_year": b.get("edition_year") or "",
                    "reprint_year": b.get("reprint_year") or "",
                    "edition_number": "",
                    "impression_number": "",
                    "impression_year": b.get("reprint_year") or str(report["year"]),
                    "category": b.get("category") or "",
                    "price": b.get("sale_price") or 0,
                    "description": "",
                    "image_data": "",
                    "included": True,
                }
                for b in books
            ]
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.route("/api/report/<report_id>/finalize", methods=["POST"])
def api_finalize_report(report_id):
    try:
        payload = request.get_json(silent=True) or {}
        path = finalize_report(report_id, payload.get("filename", ""))
        return jsonify({
            "ok": True,
            "download_url": url_for("download_report", report_id=report_id),
            "filename": get_report(report_id).get("final_filename") or path.name,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.route("/api/report/<report_id>/preview", methods=["POST"])
def api_preview_report(report_id):
    try:
        payload = request.get_json(silent=True) or {}
        _, filename = preview_report(report_id, payload.get("filename", ""))
        return jsonify({
            "ok": True,
            "preview_url": url_for("preview_report_file", report_id=report_id, filename=filename),
            "filename": filename,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400


@app.route("/report-download/<report_id>")
def download_report(report_id):
    try:
        report = get_report(report_id)
        path = Path(report.get("output_file") or "")
        if not path.exists():
            path = finalize_report(report_id)
        return send_file(path, as_attachment=True, download_name=report.get("final_filename") or path.name)
    except Exception as exc:
        return str(exc), 404


@app.route("/report-preview/<report_id>")
def preview_report_file(report_id):
    try:
        report = get_report(report_id)
        filename = report_pdf_filename(report, request.args.get("filename", ""))
        path = REPORT_DIR / "_previews" / report_id / filename
        if not path.exists():
            path, filename = preview_report(report_id, request.args.get("filename", ""))
        return send_file(path, as_attachment=False, download_name=filename, mimetype="application/pdf")
    except Exception as exc:
        return str(exc), 404


@app.route("/source-file/<source_type>")
def download_source_file(source_type):
    source = source_row(source_type)
    path = Path(source.get("active_local_path") or "")
    if not path.exists():
        return "No active source file is available.", 404
    return send_file(path, as_attachment=True, download_name=source.get("drive_file_name") or path.name)


@app.route("/setup-info")
def setup_info():
    return jsonify({
        "local_url": "http://127.0.0.1:5001",
        "drive_mode": "READ ONLY",
        "auto_sync_minutes": SYNC_INTERVAL_SECONDS // 60,
        "drive_configured": bool(GOOGLE_SERVICE_ACCOUNT_FILE),
        "note": "For local testing, import source workbooks from Database pages. Google Drive sync is optional until configured."
    })


@app.route("/health")
def health():
    return jsonify({"ok": True, "time": now_iso()})


if __name__ == "__main__":
    print("MMS Status Circular frontend and backend running at http://127.0.0.1:5001")
    print("For LAN access, open http://<server-laptop-ip>:5001")
    print("Google Drive is READ ONLY. Source files are never modified by this application.")
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False, threaded=True)
