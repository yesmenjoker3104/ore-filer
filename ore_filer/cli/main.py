from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ore_filer.services.file_operations import (
    copy_paths,
    delete_paths,
    move_paths,
    rename_path,
)
from ore_filer.services.search_service import find_paths


def _path_value(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _record(path: Path) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "is_dir": path.is_dir(),
    }
    if path.exists():
        item["size"] = path.stat().st_size if path.is_file() else None
    return item


def _list_path(path: Path) -> list[dict[str, Any]]:
    if path.is_file():
        return [_record(path)]
    if not path.is_dir():
        raise FileNotFoundError(path)
    return [
        _record(item)
        for item in sorted(
            path.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.casefold()),
        )
    ]


def _emit(payload: Any, text_lines: list[str], output_format: str) -> None:
    if output_format in {"text", "both"} and text_lines:
        print("\n".join(text_lines))
    if output_format in {"json", "both"}:
        if output_format == "both" and text_lines:
            print()
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _run(args: argparse.Namespace) -> int:
    if args.command == "list":
        path = _path_value(args.path)
        records = _list_path(path)
        _emit(records, [record["path"] for record in records], args.output_format)
        return 0

    if args.command == "search":
        root = _path_value(args.path)
        matches = find_paths(root, args.query)
        records = [_record(path) for path in matches]
        _emit(records, [record["path"] for record in records], args.output_format)
        return 0

    if args.command in {"copy", "move"}:
        destination = _path_value(args.destination)
        sources = [_path_value(value) for value in args.sources]
        missing = [path for path in sources if not path.exists()]
        if missing:
            raise FileNotFoundError(missing[0])
        if not destination.is_dir():
            raise NotADirectoryError(destination)
        operation = copy_paths if args.command == "copy" else move_paths
        operation(sources, destination, overwrite=args.overwrite)
        targets = [destination / source.name for source in sources]
        payload = {
            "operation": args.command,
            "items": [_record(path) for path in targets],
        }
        _emit(payload, [str(path) for path in targets], args.output_format)
        return 0

    if args.command == "delete":
        if not args.yes:
            raise ValueError("削除には --yes を指定してください。")
        paths = [_path_value(value) for value in args.paths]
        missing = [path for path in paths if not path.exists()]
        if missing:
            raise FileNotFoundError(missing[0])
        delete_paths(paths)
        payload = {"operation": "delete", "items": [str(path) for path in paths]}
        _emit(payload, [str(path) for path in paths], args.output_format)
        return 0

    if args.command == "rename":
        source = _path_value(args.source)
        if not source.exists():
            raise FileNotFoundError(source)
        target = rename_path(source, args.new_name)
        payload = {"operation": "rename", "items": [_record(target)]}
        _emit(payload, [str(target)], args.output_format)
        return 0

    raise ValueError(f"未対応のコマンドです: {args.command}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ore-filer")
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json", "both"),
        default="text",
        help="出力形式（既定: text）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="フォルダーの内容を一覧表示")
    list_parser.add_argument("path", nargs="?", default=".")

    search_parser = subparsers.add_parser("search", help="ファイル名を検索")
    search_parser.add_argument("path")
    search_parser.add_argument("query")

    for name, help_text in (
        ("copy", "ファイルまたはフォルダーをコピー"),
        ("move", "ファイルまたはフォルダーを移動"),
    ):
        operation_parser = subparsers.add_parser(name, help=help_text)
        operation_parser.add_argument("sources", nargs="+")
        operation_parser.add_argument("destination")
        operation_parser.add_argument("--overwrite", action="store_true")

    delete_parser = subparsers.add_parser("delete", help="ファイルまたはフォルダーを削除")
    delete_parser.add_argument("paths", nargs="+")
    delete_parser.add_argument("--yes", action="store_true")

    rename_parser = subparsers.add_parser("rename", help="ファイルまたはフォルダーをリネーム")
    rename_parser.add_argument("source")
    rename_parser.add_argument("new_name")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (OSError, ValueError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())