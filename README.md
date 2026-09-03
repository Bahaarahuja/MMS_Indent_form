# MMS Status Circular

Local web application for creating MMS Book Catalog, New Releases, Out of Stock Books, and MMS Indent reports.

## Requirements

- Python 3.10 or later
- The required source workbooks for the reports you plan to create

## Setup

From the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5001](http://127.0.0.1:5001) in a browser.

On Windows, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

## First-Time Source Setup

The application creates an empty local SQLite database at `data/mms_system.sqlite3` on first run. It also creates `data/source_files/` and `data/reports/` automatically.

Use the **Data Sources** page to upload each workbook, then review and accept it so it becomes the active source. Do not manually copy workbooks into `data/source_files/`; the application needs to import and activate them first.

Required sources by report:

| Report | Required source files |
| --- | --- |
| Book Catalog | Books Master |
| New Releases | Books Master |
| Out of Stock Books | Books Master, Books Stock Position |
| MMS Indent | Books Master, Books Stock Position, SOSRC Book List, Previous Indent, Audio Master, Audio Stock Position |

## Optional Google Drive Sync

Local workbook uploads work without Google Drive. To enable read-only Google Drive sync, set the path to a service-account JSON key before starting the app:

```bash
export GOOGLE_SERVICE_ACCOUNT_FILE="/absolute/path/to/service-account.json"
python app.py
```

Share the required Drive files with the service-account email, then configure the Drive file IDs from the Data Sources page. Never commit the service-account JSON key.

## Running Tests

```bash
python -m unittest discover -s tests -v
```

The backend test suite uses a temporary database and report directory. It does not modify the application data, uploaded workbooks, saved drafts, or downloaded reports.

## Local Data and Generated Files

The following are deliberately excluded from Git:

- `data/mms_system.sqlite3`: local source state, drafts, and report history
- `data/source_files/`: uploaded workbooks
- `data/reports/`: generated PDFs and previews
- `tmp/` and `output/`: local PDF layout verification artifacts

Each user sets up their own local data by importing the source workbooks through Data Sources.
