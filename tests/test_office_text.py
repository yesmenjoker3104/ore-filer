"""Office テキスト抽出と GREP 統合テスト。

最小限の xlsx / docx / pptx を zipfile で生成し、
extract_office_text と grep_files の動作を検証する。
"""
from __future__ import annotations

import io
import os
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ── テスト用 OOXML ファイルを生成するヘルパー ─────────────────────────────────


def _make_xlsx(path: Path, cell_value: str, drawing_text: str = "") -> None:
    """最小限の xlsx を生成する（セル A1 に cell_value、図形テキストあり）。"""
    shared_str = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="1" uniqueCount="1">'
        f'<si><t>{cell_value}</t></si>'
        '</sst>'
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
        '</sheetData>'
        '</worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        '<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
        '</sheets>'
        '</workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    drawing_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<xdr:twoCellAnchor><xdr:sp><xdr:txBody>'
        f'<a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{drawing_text}</a:t></a:r></a:p>'
        '</xdr:txBody></xdr:sp></xdr:twoCellAnchor>'
        '</xdr:wsDr>'
    ) if drawing_text else None

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared_str)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        if drawing_xml:
            zf.writestr("xl/drawings/drawing1.xml", drawing_xml)
        # [Content_Types].xml（最小）
        zf.writestr("[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')


def _make_docx(path: Path, *paragraphs: str) -> None:
    """最小限の docx を生成する。"""
    paras = "".join(
        f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs
    )
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{paras}</w:body>'
        '</w:document>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')


def _make_pptx(path: Path, *slides: str) -> None:
    """最小限の pptx を生成する（1スライド1段落）。"""
    with zipfile.ZipFile(path, "w") as zf:
        for i, text in enumerate(slides, 1):
            slide_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                '<p:cSld><p:spTree>'
                f'<p:sp><p:txBody><a:bodyPr/><a:lstStyle/>'
                f'<a:p><a:r><a:t>{text}</a:t></a:r></a:p>'
                '</p:txBody></p:sp>'
                '</p:spTree></p:cSld>'
                '</p:sld>'
            )
            zf.writestr(f"ppt/slides/slide{i}.xml", slide_xml)
        zf.writestr("[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')


# ── extract_office_text テスト ─────────────────────────────────────────────────


class ExtractXlsxTests(unittest.TestCase):
    def test_cell_value_extracted(self) -> None:
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.xlsx"
            _make_xlsx(f, "テストセル")
            from ore_filer.services.office_text import extract_office_text
            text = extract_office_text(f)
        self.assertIn("テストセル", text)

    def test_sheet_name_in_output(self) -> None:
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.xlsx"
            _make_xlsx(f, "値")
            from ore_filer.services.office_text import extract_office_text
            text = extract_office_text(f)
        self.assertIn("Sheet1", text)

    def test_cell_ref_in_output(self) -> None:
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.xlsx"
            _make_xlsx(f, "値")
            from ore_filer.services.office_text import extract_office_text
            text = extract_office_text(f)
        self.assertIn("A1", text)

    def test_drawing_text_extracted(self) -> None:
        """テキストボックス（図形）の文字列も抽出されること。"""
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.xlsx"
            _make_xlsx(f, "セル値", "テキストボックス内容")
            from ore_filer.services.office_text import extract_office_text
            text = extract_office_text(f)
        self.assertIn("テキストボックス内容", text)

    def test_bad_zip_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "bad.xlsx"
            f.write_bytes(b"not a zip file at all")
            from ore_filer.services.office_text import extract_office_text
            text = extract_office_text(f)
        self.assertEqual(text, "")

    def test_member_size_limit(self) -> None:
        """展開サイズが上限を超えるメンバーは読み飛ばされること。"""
        from ore_filer.services import office_text as _ot
        original_limit = _ot._MAX_MEMBER_BYTES
        try:
            _ot._MAX_MEMBER_BYTES = 10  # 10 バイトに下げる
            with TemporaryDirectory() as tmp:
                f = Path(tmp) / "test.xlsx"
                _make_xlsx(f, "A" * 100)
                text = _ot.extract_office_text(f)
            # shared strings が読まれないのでセル値は出ない（クラッシュしないこと）
            self.assertIsInstance(text, str)
        finally:
            _ot._MAX_MEMBER_BYTES = original_limit


class ExtractDocxTests(unittest.TestCase):
    def test_paragraphs_extracted(self) -> None:
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.docx"
            _make_docx(f, "はじめの段落", "次の段落")
            from ore_filer.services.office_text import extract_office_text
            text = extract_office_text(f)
        self.assertIn("はじめの段落", text)
        self.assertIn("次の段落", text)

    def test_each_paragraph_is_separate_line(self) -> None:
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.docx"
            _make_docx(f, "段落A", "段落B")
            from ore_filer.services.office_text import extract_office_text
            lines = extract_office_text(f).splitlines()
        self.assertIn("段落A", lines)
        self.assertIn("段落B", lines)
        # 別々の行に出力されていること
        self.assertNotEqual(lines.index("段落A"), lines.index("段落B"))


class ExtractPptxTests(unittest.TestCase):
    def test_slide_text_extracted(self) -> None:
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.pptx"
            _make_pptx(f, "スライド1のテキスト", "スライド2のテキスト")
            from ore_filer.services.office_text import extract_office_text
            text = extract_office_text(f)
        self.assertIn("スライド1のテキスト", text)
        self.assertIn("スライド2のテキスト", text)

    def test_slide_prefix_in_output(self) -> None:
        """出力行に「スライドN: 」プレフィックスが付くこと。"""
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.pptx"
            _make_pptx(f, "テスト")
            from ore_filer.services.office_text import extract_office_text
            text = extract_office_text(f)
        self.assertIn("スライド1:", text)


# ── grep_files 統合テスト ─────────────────────────────────────────────────────


class GrepOfficeFilesTests(unittest.TestCase):
    def test_grep_finds_cell_value(self) -> None:
        """grep_files が xlsx のセル値をヒットすること。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "data.xlsx"
            _make_xlsx(f, "検索対象文字列")
            from ore_filer.services.search_service import grep_files
            results = grep_files(root, "検索対象文字列")
        self.assertTrue(any(r.path == f for r in results), "xlsx がヒットしていない")

    def test_grep_finds_docx_text(self) -> None:
        """grep_files が docx の段落テキストをヒットすること。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "doc.docx"
            _make_docx(f, "ドキュメント本文テキスト")
            from ore_filer.services.search_service import grep_files
            results = grep_files(root, "ドキュメント本文テキスト")
        self.assertTrue(any(r.path == f for r in results), "docx がヒットしていない")

    def test_grep_finds_pptx_text(self) -> None:
        """grep_files が pptx のスライドテキストをヒットすること。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "pres.pptx"
            _make_pptx(f, "プレゼンテーションの内容")
            from ore_filer.services.search_service import grep_files
            results = grep_files(root, "プレゼンテーションの内容")
        self.assertTrue(any(r.path == f for r in results), "pptx がヒットしていない")

    def test_grep_lineno_matches_extract_lineno(self) -> None:
        """grep_files の lineno が extract_office_text の行番号と一致すること。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "data.xlsx"
            _make_xlsx(f, "ユニークな値")
            from ore_filer.services.search_service import grep_files
            from ore_filer.services.office_text import extract_office_text
            results = grep_files(root, "ユニークな値")
            hit = next(r for r in results if r.path == f)
            lines = extract_office_text(f).splitlines()
            # 1-indexed
            self.assertIn("ユニークな値", lines[hit.lineno - 1])

    def test_drawing_text_searchable(self) -> None:
        """テキストボックスの文字列も GREP で検索できること。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "with_shapes.xlsx"
            _make_xlsx(f, "セル", "テキストボックスの中身")
            from ore_filer.services.search_service import grep_files
            results = grep_files(root, "テキストボックスの中身")
        self.assertTrue(any(r.path == f for r in results))


if __name__ == "__main__":
    unittest.main()
