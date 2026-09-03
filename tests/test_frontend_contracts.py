import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTestCase(unittest.TestCase):
    """Catch accidental removal of the page controls that users rely on."""

    def page_text(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_create_pages_keep_shared_draft_navigation(self):
        for page in [
            "frontend/book-catalog/index.html",
            "frontend/new-releases/index.html",
            "frontend/out-of-stock/index.html",
        ]:
            contents = self.page_text(page)
            self.assertIn("Start or continue", contents)
            self.assertIn("Continue a draft", contents)
            self.assertIn("catalog-workspace", contents)

    def test_new_release_and_out_of_stock_keep_book_search_controls(self):
        for page in ["frontend/new-releases/index.html", "frontend/out-of-stock/index.html"]:
            contents = self.page_text(page)
            self.assertIn("Search books", contents)
            self.assertIn("Add by Code", contents)
            self.assertIn("Add Blank", contents)
            self.assertIn("Remove Selected", contents)
            self.assertIn("Audit trail", contents)

    def test_frontend_scripts_parse(self):
        for script in ["frontend/js/app.js", "frontend/js/pages.js", "frontend/js/report.js"]:
            result = subprocess.run(
                ["node", "--check", script],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_releases_pdf_keeps_type_and_price_in_their_own_columns(self):
        source = self.page_text("app.py")
        self.assertIn('Paragraph("<u>TYPE</u>",header_style)', source)
        self.assertIn('Paragraph(book_type,type_style)', source)
        self.assertIn('fontName="Times-Italic",textColor=colors.blue', source)
        self.assertIn('Paragraph(f"Rs. {to_number(row.get(\'price\')):,.2f}",price_style)', source)

    def test_new_releases_quick_search_uses_prefix_api_and_explicit_filename(self):
        source = self.page_text("frontend/js/report.js")
        self.assertIn('/api/books/search?q=${encodeURIComponent(value)}', source)
        self.assertIn('/api/books/resolve?code=${encodeURIComponent(raw)}', source)
        self.assertIn('link.download=filename||"report.pdf"', source)

    def test_report_pages_keep_preview_and_selection_safe_catalog_editor(self):
        for page in [
            "frontend/book-catalog/index.html",
            "frontend/new-releases/index.html",
            "frontend/out-of-stock/index.html",
        ]:
            self.assertIn('onclick="previewPdf()"', self.page_text(page))
        source = self.page_text("frontend/js/report.js")
        self.assertIn('draggable="true" ondragstart="dragStart(${i},event)"', source)
        self.assertIn('onmousedown="event.preventDefault();formatDescription(${i}', source)
        self.assertIn('function bindCatalogDescriptionEditors()', source)
        self.assertIn('editor.addEventListener("paste",event=>pasteDescription(index,event))', source)
        self.assertIn('/api/report/${currentReport.id}/preview', source)

    def test_report_builders_keep_explicit_draft_saves_and_history_bulk_actions(self):
        for page in [
            "frontend/book-catalog/index.html",
            "frontend/new-releases/index.html",
            "frontend/out-of-stock/index.html",
        ]:
            self.assertIn("Save as Draft", self.page_text(page))
        report_source = self.page_text("frontend/js/report.js")
        self.assertIn("requestBookApply(rowIndex,exact,{removeOnReject:true})", report_source)
        self.assertIn("setValidationGroups(true)", report_source)
        history_source = self.page_text("frontend/js/pages.js")
        self.assertIn("Delete Selected", history_source)
        self.assertIn("Delete All Drafts", history_source)
        self.assertIn("Delete All Finalized", history_source)
        self.assertIn("/api/reports/delete-bulk", history_source)

    def test_report_preferences_and_settings_are_not_reset_by_navigation(self):
        report_source = self.page_text("frontend/js/report.js")
        pages_source = self.page_text("frontend/js/pages.js")
        styles = self.page_text("frontend/css/style.css")
        self.assertIn("savePreferredFileName(e.target.value)", report_source)
        self.assertIn("function updateReportSettings()", report_source)
        self.assertIn("month:currentReport.month", report_source)
        self.assertIn("loadPreferredFileName()", pages_source)
        self.assertIn("recentDraftListVersion", pages_source)
        self.assertIn("text-transform:none", styles)

    def test_draft_delete_browser_flow(self):
        result = subprocess.run(
            ["node", "--test", "tests/frontend_delete_flow.test.js"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
