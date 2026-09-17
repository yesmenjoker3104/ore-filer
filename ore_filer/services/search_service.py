import os
from collections.abc import Callable
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
