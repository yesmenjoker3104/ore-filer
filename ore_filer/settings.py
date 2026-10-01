import json
import os
from pathlib import Path
from typing import Any


MAX_HISTORY = 200


def session_file() -> Path:
	appdata = os.environ.get("APPDATA")
	base = Path(appdata) if appdata else Path.home() / ".config"
	return base / "ore-filer" / "session.json"


def load_session() -> dict:
	empty: dict = {"history": [], "geometry": "", "splitter": ""}
	try:
		with session_file().open("r", encoding="utf-8") as file:
			data: Any = json.load(file)
	except (OSError, ValueError, TypeError):
		return empty

	if not isinstance(data, dict):
		return empty

	values = data.get("history", [])
	if not isinstance(values, list):
		values = []
	if not values:
		left_values = data.get("left_history", [])
		right_values = data.get("right_history", [])
		values = (
			(left_values if isinstance(left_values, list) else [])
			+ (right_values if isinstance(right_values, list) else [])
		)
	def _str(key: str) -> str:
		v = data.get(key, "")
		return v if isinstance(v, str) else ""

	return {
		"history": [value for value in values if isinstance(value, str)][:MAX_HISTORY],
		"left_path": _str("left_path"),
		"right_path": _str("right_path"),
		"geometry": _str("geometry"),
		"splitter": _str("splitter"),
		"last_update_check": _str("last_update_check"),
	}


def save_session(
	history: list[Path],
	*,
	left_path: str = "",
	right_path: str = "",
	geometry: str = "",
	splitter: str = "",
	last_update_check: str = "",
) -> None:
	path = session_file()
	path.parent.mkdir(parents=True, exist_ok=True)
	data = {
		"history": [str(item) for item in history[:MAX_HISTORY]],
		"left_path": left_path,
		"right_path": right_path,
		"geometry": geometry,
		"splitter": splitter,
		"last_update_check": last_update_check,
	}
	temporary_path = path.with_suffix(".tmp")
	with temporary_path.open("w", encoding="utf-8") as file:
		json.dump(data, file, ensure_ascii=False, indent=2)
	temporary_path.replace(path)


def _bookmark_file() -> Path:
	appdata = os.environ.get("APPDATA")
	base = Path(appdata) if appdata else Path.home() / ".config"
	return base / "ore-filer" / "bookmarks.json"


def load_bookmarks() -> list[str]:
	try:
		with _bookmark_file().open("r", encoding="utf-8") as f:
			data = json.load(f)
		if isinstance(data, list):
			return [item for item in data if isinstance(item, str)]
	except (OSError, ValueError, TypeError):
		pass
	return []


def save_bookmarks(bookmarks: list[str]) -> None:
	path = _bookmark_file()
	path.parent.mkdir(parents=True, exist_ok=True)
	tmp = path.with_suffix(".tmp")
	with tmp.open("w", encoding="utf-8") as f:
		json.dump(bookmarks, f, ensure_ascii=False, indent=2)
	tmp.replace(path)
