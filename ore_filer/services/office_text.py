"""Office ドキュメント（xlsx / docx / pptx）からテキストを抽出する。

OOXML は ZIP アーカイブなので zipfile で開き、
xml.etree.ElementTree で各 XML メンバーを解析する。
"""
from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


# サポートする Office 拡張子
OFFICE_EXTENSIONS = frozenset({
    ".xlsx", ".xlsm",          # Excel
    ".docx", ".dotx",          # Word
    ".pptx", ".potx",          # PowerPoint
})

# メンバーの展開サイズ上限（zip bomb 対策）
_MAX_MEMBER_BYTES = 50 * 1024 * 1024  # 50 MB


# ── 内部ユーティリティ ────────────────────────────────────────


def _tag(el: ET.Element) -> str:
    """Clark 記法 {uri}localName → localName を返す。"""
    tag = el.tag
    return tag.split("}")[-1] if "}" in tag else tag


def _read_member(zf: zipfile.ZipFile, name: str) -> str | None:
    """ZipFile からメンバーを安全に読み込んでテキストとして返す。
    存在しない・サイズ超過・デコード失敗時は None を返す。"""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_MEMBER_BYTES:
        return None  # zip bomb 対策
    try:
        return zf.read(name).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None


def _parse(xml_text: str) -> ET.Element | None:
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None


def _members_with_prefix(zf: zipfile.ZipFile, prefix: str) -> list[str]:
    return sorted(m for m in zf.namelist() if m.startswith(prefix))


# ── xlsx ──────────────────────────────────────────────────────


def _extract_xlsx(zf: zipfile.ZipFile) -> str:
    lines: list[str] = []

    # 1. 共有文字列テーブル（sharedStrings.xml）
    shared: list[str] = []
    ss_text = _read_member(zf, "xl/sharedStrings.xml")
    if ss_text:
        root = _parse(ss_text)
        if root is not None:
            for si in root:
                if _tag(si) == "si":
                    # rich text (<r><t>…</t></r>) も単純 (<t>…</t>) もまとめて結合
                    shared.append("".join(
                        el.text or "" for el in si.iter() if _tag(el) == "t"
                    ))

    # 2. シート名マップ（workbook.xml）: rId → sheet name
    sheet_names: dict[str, str] = {}
    wb_text = _read_member(zf, "xl/workbook.xml")
    if wb_text:
        root = _parse(wb_text)
        if root is not None:
            _R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            for el in root.iter():
                if _tag(el) == "sheet":
                    r_id = el.get(f"{{{_R_NS}}}id", "")
                    name = el.get("name", r_id)
                    if r_id:
                        sheet_names[r_id] = name

    # 3. リレーションシップ（workbook.xml.rels）: rId → ファイルパス
    rid_to_path: dict[str, str] = {}
    rels_text = _read_member(zf, "xl/_rels/workbook.xml.rels")
    if rels_text:
        root = _parse(rels_text)
        if root is not None:
            for rel in root:
                r_id = rel.get("Id", "")
                target = rel.get("Target", "")
                if not (r_id and target):
                    continue
                # Target が絶対パス（/xl/…）か相対パス（worksheets/…）かを統一
                if target.startswith("/"):
                    target = target.lstrip("/")
                elif not target.startswith("xl/"):
                    target = "xl/" + target
                rid_to_path[r_id] = target

    # 4. 各シートのセルを走査
    for r_id, sheet_path in sorted(rid_to_path.items()):
        sheet_name = sheet_names.get(r_id, r_id)
        sheet_text = _read_member(zf, sheet_path)
        if sheet_text is None:
            continue
        root = _parse(sheet_text)
        if root is None:
            continue
        for c_el in root.iter():
            if _tag(c_el) != "c":
                continue
            ref = c_el.get("r", "")
            t = c_el.get("t", "")
            value = ""
            if t == "s":  # 共有文字列インデックス
                v_el = next((e for e in c_el if _tag(e) == "v"), None)
                if v_el is not None and v_el.text is not None:
                    try:
                        idx = int(v_el.text)
                        if 0 <= idx < len(shared):
                            value = shared[idx]
                    except ValueError:
                        pass
            elif t in ("str", "inlineStr"):
                # 数式の結果文字列、またはインライン文字列
                value = "".join(
                    el.text or "" for el in c_el.iter() if _tag(el) == "t"
                )
            else:
                # 数値・日付・ブール等は v 要素のテキストをそのまま
                v_el = next((e for e in c_el if _tag(e) == "v"), None)
                if v_el is not None and v_el.text:
                    value = v_el.text
            if value:
                lines.append(f"{sheet_name}!{ref}\t{value}")

    # 5. 図形・テキストボックス（xl/drawings/*.xml の <a:p> 単位）
    for member in _members_with_prefix(zf, "xl/drawings/"):
        if not member.endswith(".xml"):
            continue
        txt = _read_member(zf, member)
        if not txt:
            continue
        root = _parse(txt)
        if root is None:
            continue
        for p_el in root.iter():
            if _tag(p_el) == "p":
                para = "".join(
                    el.text or "" for el in p_el.iter() if _tag(el) == "t"
                ).strip()
                if para:
                    lines.append(para)

    # 6. コメント（xl/comments*.xml）
    for member in _members_with_prefix(zf, "xl/"):
        if not (member.startswith("xl/comments") and member.endswith(".xml")):
            continue
        txt = _read_member(zf, member)
        if not txt:
            continue
        root = _parse(txt)
        if root is None:
            continue
        for el in root.iter():
            if _tag(el) == "text":
                comment = "".join(
                    sub.text or "" for sub in el.iter() if _tag(sub) == "t"
                ).strip()
                if comment:
                    lines.append(comment)

    return "\n".join(lines)


# ── docx ──────────────────────────────────────────────────────


def _extract_docx(zf: zipfile.ZipFile) -> str:
    lines: list[str] = []
    members = ["word/document.xml"]
    members += _members_with_prefix(zf, "word/header")
    members += _members_with_prefix(zf, "word/footer")

    for member in members:
        if not member.endswith(".xml"):
            continue
        txt = _read_member(zf, member)
        if not txt:
            continue
        root = _parse(txt)
        if root is None:
            continue
        # <w:p> ごとにまとめて1行にする
        for p_el in root.iter():
            if _tag(p_el) == "p":
                # <w:t> テキストを結合（テキストボックス内も含む）
                para = "".join(
                    el.text or "" for el in p_el.iter() if _tag(el) == "t"
                )
                if para.strip():
                    lines.append(para)

    return "\n".join(lines)


# ── pptx ──────────────────────────────────────────────────────


def _extract_pptx(zf: zipfile.ZipFile) -> str:
    lines: list[str] = []
    slide_members = [
        m for m in _members_with_prefix(zf, "ppt/slides/")
        if m.startswith("ppt/slides/slide") and m.endswith(".xml")
    ]

    for i, member in enumerate(slide_members, 1):
        txt = _read_member(zf, member)
        if not txt:
            continue
        root = _parse(txt)
        if root is None:
            continue
        for p_el in root.iter():
            if _tag(p_el) == "p":
                para = "".join(
                    el.text or "" for el in p_el.iter() if _tag(el) == "t"
                ).strip()
                if para:
                    lines.append(f"スライド{i}: {para}")

    return "\n".join(lines)


# ── 公開 API ──────────────────────────────────────────────────


def extract_office_text(path: Path) -> str:
    """Office ドキュメントからテキストを抽出する。失敗時は空文字列を返す。"""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            suffix = path.suffix.casefold()
            if suffix in (".xlsx", ".xlsm"):
                return _extract_xlsx(zf)
            elif suffix in (".docx", ".dotx"):
                return _extract_docx(zf)
            elif suffix in (".pptx", ".potx"):
                return _extract_pptx(zf)
            return ""
    except (zipfile.BadZipFile, OSError):
        return ""
