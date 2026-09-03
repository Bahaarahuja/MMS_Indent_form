import json
import tempfile
import unittest
from pathlib import Path

import app as app_module


class ReportApiTestCase(unittest.TestCase):
    """Exercise report APIs against isolated files, never the live application data."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_paths = {
            "DB_PATH": app_module.DB_PATH,
            "DATA_DIR": app_module.DATA_DIR,
            "SOURCE_DIR": app_module.SOURCE_DIR,
            "REPORT_DIR": app_module.REPORT_DIR,
        }
        app_module.DATA_DIR = root / "data"
        app_module.SOURCE_DIR = app_module.DATA_DIR / "source_files"
        app_module.REPORT_DIR = app_module.DATA_DIR / "reports"
        app_module.DB_PATH = app_module.DATA_DIR / "mms_system.sqlite3"
        app_module.DATA_DIR.mkdir()
        app_module.SOURCE_DIR.mkdir()
        app_module.REPORT_DIR.mkdir()
        app_module.init_db()
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(app_module, name, value)
        self.temp_dir.cleanup()

    def make_source_ready(self, source_type):
        with app_module.db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO source_versions(source_type, synced_at, status, record_count)
                VALUES (?, ?, 'ACTIVE', 1)
                """,
                (source_type, app_module.now_iso()),
            )
            conn.execute(
                """
                UPDATE sources
                SET active_version_id=?, last_status='ACTIVE', record_count=1
                WHERE source_type=?
                """,
                (cursor.lastrowid, source_type),
            )

    def add_book(self, code="EN-300-0-01-02", mms_code="MMS-1", category="INDIAN"):
        with app_module.db() as conn:
            conn.execute(
                """
                INSERT INTO books_master(
                    mms_code, bav_code, book_name, language, language_category,
                    edition_year, reprint_year, sale_price, author, category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (mms_code, code, "One Cup of Sand", "English", category, "2024", "2026", 30, "Beverly Chapman", "Books"),
            )

    def create_report(self, report_type, month=9, year=2026):
        response = self.client.post(
            "/api/report/generate",
            json={"report_type": report_type, "market": "INDIAN", "month": month, "year": year},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["report"]

    def test_report_creation_requires_active_sources(self):
        response = self.client.post(
            "/api/report/generate",
            json={"report_type": "new-releases", "market": "INDIAN", "month": 9, "year": 2026},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Books Master", response.get_json()["message"])

    def test_book_search_honors_market_and_normalized_bav_codes(self):
        self.add_book(code="EN-300-0-01-02", mms_code="MMS-IND", category="INDIAN")
        self.add_book(code="FR-400-0-01-01", mms_code="MMS-FOR", category="FOREIGN")

        indian = self.client.get("/api/books/search?q=en300&market=INDIAN").get_json()
        self.assertTrue(indian["ok"])
        self.assertEqual([book["code"] for book in indian["books"]], ["EN-300-0-01-02"])

        foreign = self.client.get("/api/books/search?q=fr400&market=INDIAN").get_json()
        self.assertEqual(foreign["books"], [])

        overseas = self.client.get("/api/books/search?q=fr400&market=OVERSEAS").get_json()
        self.assertEqual([book["code"] for book in overseas["books"]], ["FR-400-0-01-01"])

    def test_new_release_draft_can_finalize_download_and_delete(self):
        self.make_source_ready("BOOK_MASTER")
        self.add_book()
        report = self.create_report("new-releases")
        draft = report["draft"]
        draft["rows"] = [{
            "id": "row-1", "included": True, "selected": False,
            "code": "EN-300-0-01-02", "mms_code": "MMS-1", "title": "One Cup of Sand",
            "type": "Reprint", "author": "Beverly Chapman", "language": "English",
            "language_category": "INDIAN", "category": "Books", "price": 30,
            "edition_number": "1", "edition_year": "2024",
            "impression_number": "2", "impression_year": "2026",
        }]
        update = self.client.put(f"/api/report/{report['id']}", json=draft)
        self.assertEqual(update.status_code, 200, update.get_json())

        finalized = self.client.post(
            f"/api/report/{report['id']}/finalize",
            json={"filename": "My New Releases - September 2026"},
        )
        self.assertEqual(finalized.status_code, 200, finalized.get_json())
        self.assertEqual(finalized.get_json()["filename"], "My New Releases - September 2026.pdf")

        saved = self.client.get(f"/api/report/{report['id']}").get_json()["report"]
        self.assertEqual(saved["status"], "FINAL")
        output_path = Path(saved["output_file"])
        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.name, "My New Releases - September 2026.pdf")
        self.assertEqual(output_path.parent.name, report["id"])
        self.assertTrue(output_path.read_bytes().startswith(b"%PDF"))

        download = self.client.get(f"/report-download/{report['id']}")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.data.startswith(b"%PDF"))
        self.assertIn("My New Releases - September 2026.pdf", download.headers["Content-Disposition"])
        download.close()

        continuation = self.client.get("/api/frontend/report/new-releases").get_json()["recent_drafts"]
        self.assertIn(report["id"], [item["id"] for item in continuation])

        deleted = self.client.post(f"/api/report/{report['id']}/delete", json={})
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertFalse(output_path.exists())
        self.assertFalse(self.client.get(f"/api/report/{report['id']}").get_json()["ok"])

    def test_catalog_preview_keeps_draft_and_uses_requested_filename(self):
        self.make_source_ready("BOOK_MASTER")
        report = self.create_report("book-catalog")
        draft = report["draft"]
        draft["rows"] = [{
            "id": "row-1", "included": True, "selected": False,
            "code": "EN-300-0-01-02", "title": "One Cup of Sand", "author": "Beverly Chapman",
            "language": "English", "category": "Books for Children",
            "description_html": "A <b>formatted</b> description.", "description_font_size": 11,
        }]
        self.client.put(f"/api/report/{report['id']}", json=draft)

        preview = self.client.post(
            f"/api/report/{report['id']}/preview",
            json={"filename": "Catalog Proof"},
        )
        self.assertEqual(preview.status_code, 200, preview.get_json())
        self.assertEqual(preview.get_json()["filename"], "Catalog Proof.pdf")
        self.assertIn("filename=Catalog+Proof.pdf", preview.get_json()["preview_url"])

        saved = self.client.get(f"/api/report/{report['id']}").get_json()["report"]
        self.assertEqual(saved["status"], "DRAFT")
        self.assertIsNone(saved["output_file"])

        rendered = self.client.get(preview.get_json()["preview_url"])
        self.assertEqual(rendered.status_code, 200)
        self.assertTrue(rendered.data.startswith(b"%PDF"))
        self.assertIn("inline", rendered.headers["Content-Disposition"])
        rendered.close()

    def test_create_and_drafts_lists_every_unfinished_draft(self):
        self.make_source_ready("BOOK_MASTER")
        for _ in range(5):
            report = self.create_report("book-catalog")
            draft = report["draft"]
            draft["rows"] = [{"id": report["id"], "code": "EN-300-0-01-02", "title": "One Cup of Sand"}]
            self.client.put(f"/api/report/{report['id']}", json=draft)

        response = self.client.get("/api/frontend/report/book-catalog")
        self.assertEqual(response.status_code, 200, response.get_json())
        drafts = response.get_json()["recent_drafts"]
        self.assertEqual(len(drafts), 5)
        self.assertEqual([draft["row_count"] for draft in drafts], [1, 1, 1, 1, 1])

    def test_empty_new_report_is_not_kept_as_a_draft(self):
        self.make_source_ready("BOOK_MASTER")
        report = self.create_report("new-releases")

        listing = self.client.get("/api/frontend/report/new-releases").get_json()["recent_drafts"]
        self.assertNotIn(report["id"], [item["id"] for item in listing])
        self.assertFalse(self.client.get(f"/api/report/{report['id']}").get_json()["ok"])

    def test_bulk_delete_can_limit_drafts_to_one_report_type(self):
        self.make_source_ready("BOOK_MASTER")
        new_release = self.create_report("new-releases")
        catalog = self.create_report("book-catalog")
        for report in [new_release, catalog]:
            draft = report["draft"]
            draft["rows"] = [{"id": report["id"], "code": "EN-300-0-01-02", "title": "One Cup of Sand"}]
            self.client.put(f"/api/report/{report['id']}", json=draft)

        deleted = self.client.post(
            "/api/reports/delete-bulk",
            json={"status": "DRAFT", "report_type": "new-releases"},
        )
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertEqual(deleted.get_json()["deleted"], 1)
        self.assertFalse(self.client.get(f"/api/report/{new_release['id']}").get_json()["ok"])
        self.assertTrue(self.client.get(f"/api/report/{catalog['id']}").get_json()["ok"])

    def test_new_release_years_are_limited_to_four_digits(self):
        self.make_source_ready("BOOK_MASTER")
        report = self.create_report("new-releases")
        draft = report["draft"]
        draft["rows"] = [{"id": "row-1", "edition_year": "20266", "impression_year": "20ab26"}]

        updated = self.client.put(f"/api/report/{report['id']}", json=draft)
        self.assertEqual(updated.status_code, 200, updated.get_json())
        saved = self.client.get(f"/api/report/{report['id']}").get_json()["report"]["draft"]["rows"][0]
        self.assertEqual(saved["edition_year"], "2026")
        self.assertEqual(saved["impression_year"], "2026")

    def test_report_settings_are_saved_with_the_draft(self):
        self.make_source_ready("BOOK_MASTER")
        report = self.create_report("book-catalog")
        draft = report["draft"]
        draft["rows"] = [{"id": "row-1", "code": "EN-300-0-01-02", "title": "One Cup of Sand"}]

        updated = self.client.put(
            f"/api/report/{report['id']}",
            json={"draft": draft, "market": "OVERSEAS", "month": 10, "year": 2027},
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        saved = self.client.get(f"/api/report/{report['id']}").get_json()["report"]
        self.assertEqual(saved["market"], "OVERSEAS")
        self.assertEqual(saved["month"], 10)
        self.assertEqual(saved["year"], 2027)

    def test_out_of_stock_validation_uses_current_stock_rule(self):
        self.make_source_ready("BOOK_MASTER")
        self.make_source_ready("BOOK_STOCK")
        self.add_book(code="EN-100-0-01-01", mms_code="MMS-OOS")
        with app_module.db() as conn:
            conn.execute(
                """
                INSERT INTO books_stock(mms_code, total_hq_dl_stock, pb_hq_dera_stalls, raw_json)
                VALUES ('MMS-OOS', 4, 5, '{}')
                """
            )

        report = self.create_report("out-of-stock")
        draft = report["draft"]
        draft["rows"] = [{"id": "row-1", "included": True, "code": "EN-100-0-01-01", "title": "One Cup of Sand"}]
        self.client.put(f"/api/report/{report['id']}", json=draft)

        response = self.client.post(f"/api/report/{report['id']}/validate-out-of-stock", json={})
        self.assertEqual(response.status_code, 200, response.get_json())
        validation = response.get_json()["report"]["draft"]["meta"]["validation"]
        self.assertEqual(validation["expected_count"], 1)
        self.assertEqual(validation["missing"], [])
        self.assertEqual(validation["reviewed"][0]["combined_stock"], 9)


if __name__ == "__main__":
    unittest.main()
