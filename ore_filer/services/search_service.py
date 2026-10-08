import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path


def find_paths(
	root: str | Path,
	query: str,
	*,
	is_cancelled: Callable[[], bool] | None = None,
) -> list[Path]:
	root = Path(root).expanduser().resolve()
	query = query.strip().casefold()
	if not query or not root.is_dir():
		return []
	pattern = query if "*" in query or "?" in query else f"*{query}*"

	matches: list[Path] = []
	for current_root, directory_names, file_names in os.walk(
		root,
		topdown=True,
		followlinks=False,
	):
		if is_cancelled is not None and is_cancelled():
			break

		directory_names.sort(key=str.casefold)
		file_names.sort(key=str.casefold)
		for name in [*directory_names, *file_names]:
			if is_cancelled is not None and is_cancelled():
				return matches
			if fnmatchcase(name.casefold(), pattern):
				matches.append(Path(current_root) / name)

	return matches


# ── GREP ────────────────────────────────────────────────────


@dataclass
class GrepMatch:
    path: Path
    lineno: int
    line: str


_ENCODINGS = ("utf-8-sig", "utf-8", "cp932", "latin-1")


def _decode_bytes(raw: bytes) -> tuple[str, str] | None:
    """バイト列をデコードする。バイナリ判定なら None を返す。"""
    if b"\x00" in raw:
        return None  # バイナリ
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def grep_files(
    root: str | Path,
    pattern: str,
    *,
    recursive: bool = True,
    use_regex: bool = False,
    ignore_case: bool = True,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[GrepMatch]:
    """ファイル内容をテキスト検索し、マッチした行のリストを返す（cfiler 準拠）。"""
    root = Path(root).expanduser().resolve()
    if not pattern or not root.is_dir():
        return []

    flags = re.IGNORECASE if ignore_case else 0
    if use_regex:
        try:
            compiled = re.compile(pattern, flags)
        except re.error:
            return []
        def _match(line: str) -> bool:
            return bool(compiled.search(line))
    else:
        needle = pattern.lower() if ignore_case else pattern
        def _match(line: str) -> bool:
            hay = line.lower() if ignore_case else line
            return needle in hay

    matches: list[GrepMatch] = []
    walker = os.walk(root, topdown=True, followlinks=False)

    for current_root, dir_names, file_names in walker:
        if is_cancelled is not None and is_cancelled():
            break
        dir_names.sort(key=str.casefold)
        if not recursive:
            dir_names.clear()

        for name in sorted(file_names, key=str.casefold):
            if is_cancelled is not None and is_cancelled():
                return matches
            file_path = Path(current_root) / name
            try:
                raw = file_path.read_bytes()
            except OSError:
                continue
            decoded = _decode_bytes(raw)
            if decoded is None:
                continue  # バイナリはスキップ
            text, _ = decoded
            for lineno, line in enumerate(text.splitlines(), 1):
                if _match(line):
                    matches.append(GrepMatch(file_path, lineno, line.rstrip()))

    return matches
