# Testing

Run the full regression suite from the project root:

```bash
python -m unittest discover -s tests -v
```

The suite is safe to run locally. Each backend test uses a temporary SQLite database and temporary report directory, so it does not modify the app's active source files, saved drafts, or generated downloads.

Coverage includes:

- Required-source protection before report generation.
- BAV search normalization and market filtering.
- New Releases draft creation, save, final PDF generation, download, and deletion.
- Client-side draft deletion flows, including stale draft-list recovery.
- Out of Stock validation against the current stock rule.
- Shared create/draft navigation and essential book-search UI controls.
- JavaScript syntax checks for the shared frontend scripts.
