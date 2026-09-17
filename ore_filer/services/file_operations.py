import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import stat
import tarfile
from tempfile import TemporaryDirectory
import zipfile


class ArchiveFormatError(ValueError):
    pass


class ArchivePasswordError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    member_path: str
    is_dir: bool
    size: int = 0


def _archive_format(path: Path) -> str:
    name = path.name.casefold()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "tar.gz"
    if name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return "tar.bz2"
    if name.endswith(".tar"):
        return "tar"
    if name.endswith((".zip", ".jar", ".apk")):
        return "zip"
    if name.endswith(".7z"):
        return "7z"
    raise ArchiveFormatError(
        "対応している形式は .zip、.jar、.apk、.7z、.tar、.tar.gz、.tgz、.tar.bz2、.tbz2 です。"
    )


def is_archive_path(path: str | Path) -> bool:
    try:
        _archive_format(Path(path))
    except ArchiveFormatError:
        return False
    return True


def _normalize_member_name(member_name: str) -> str:
    normalized_name = str(member_name).replace("\\", "/")
    if normalized_name.startswith("/") or (
        len(normalized_name) >= 2 and normalized_name[1] == ":"
    ):
        raise ValueError(f"安全でないアーカイブパスです: {member_name}")

    parts = [
        part
        for part in normalized_name.split("/")
        if part not in {"", "."}
    ]
    if any(part == ".." or ":" in part for part in parts):
        raise ValueError(f"安全でないアーカイブパスです: {member_name}")
    return "/".join(parts)


def _archive_member_metadata(
    archive_path: Path,
    password: str | None,
):
    archive_type = _archive_format(archive_path)
    if archive_type == "zip":
        try:
            import pyzipper

            with pyzipper.AESZipFile(archive_path, mode="r") as archive:
                infos = archive.infolist()
                encrypted_infos = [
                    info
                    for info in infos
                    if info.flag_bits & 0x1
                ]
                if encrypted_infos and not password:
                    raise ArchivePasswordError(
                        "このアーカイブを開くにはパスワードが必要です。"
                    )
                if encrypted_infos and password:
                    first_file = next(
                        (
                            info
                            for info in encrypted_infos
                            if not info.is_dir()
                        ),
                        None,
                    )
                    if first_file is not None:
                        try:
                            archive.setpassword(password.encode("utf-8"))
                            with archive.open(first_file, mode="r") as source:
                                source.read(1)
                        except Exception as error:
                            raise ArchivePasswordError(
                                "アーカイブのパスワードが正しくありません。"
                            ) from error

                for info in infos:
                    name = _normalize_member_name(info.filename)
                    if not name:
                        continue
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise ValueError(
                            f"リンクを含むアーカイブは閲覧できません: {info.filename}"
                        )
                    yield name, info.is_dir(), info.file_size
            return
        except ArchivePasswordError:
            raise
        except ModuleNotFoundError as error:
            raise ArchiveFormatError(
                ".zipの閲覧にはpyzipperが必要です。"
            ) from error

    if archive_type in {"tar", "tar.gz", "tar.bz2"}:
        mode = {
            "tar": "r",
            "tar.gz": "r:gz",
            "tar.bz2": "r:bz2",
        }[archive_type]
        with tarfile.open(archive_path, mode=mode) as archive:
            for member in archive.getmembers():
                name = _normalize_member_name(member.name)
                if not name:
                    continue
                if member.issym() or member.islnk():
                    raise ValueError(
                        f"リンクを含むアーカイブは閲覧できません: {member.name}"
                    )
                if not member.isdir() and not member.isfile():
                    raise ValueError(f"対応していない項目です: {member.name}")
                yield name, member.isdir(), member.size
        return

    try:
        import py7zr
    except ModuleNotFoundError as error:
        raise ArchiveFormatError(
            ".7zの閲覧にはpy7zrが必要です。"
        ) from error

    try:
        with py7zr.SevenZipFile(
            archive_path,
            mode="r",
            password=password,
        ) as archive:
            for info in archive.list():
                name = _normalize_member_name(info.filename)
                if not name:
                    continue
                yield name, info.is_directory, int(info.uncompressed or 0)
    except ArchivePasswordError:
        raise
    except Exception as error:
        raise ArchivePasswordError(
            "アーカイブのパスワードが必要か、正しくありません。"
        ) from error


def _archive_records(
    archive_path: Path,
    password: str | None,
) -> tuple[dict[str, tuple[bool, int]], set[str]]:
    records: dict[str, tuple[bool, int]] = {}
    actual_members: set[str] = set()
    for name, is_dir, size in _archive_member_metadata(archive_path, password):
        actual_members.add(name)
        parts = name.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            records.setdefault(parent, (True, 0))
        records[name] = (is_dir, size)
    return records, actual_members


def list_archive_entries(
    archive_path: str | Path,
    member_dir: str = "",
    *,
    password: str | None = None,
) -> list[ArchiveEntry]:
    archive_path = Path(archive_path).expanduser().resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)

    current_dir = _normalize_member_name(member_dir)
    records, _ = _archive_records(archive_path, password)
    if current_dir and records.get(current_dir, (False, 0))[0] is False:
        raise NotADirectoryError(current_dir)

    entries: dict[str, ArchiveEntry] = {}
    prefix = f"{current_dir}/" if current_dir else ""
    for member_path, (is_dir, size) in records.items():
        if prefix and not member_path.startswith(prefix):
            continue
        if prefix:
            relative_path = member_path[len(prefix):]
        else:
            relative_path = member_path
        if not relative_path:
            continue

        name = relative_path.split("/", 1)[0]
        child_path = f"{current_dir}/{name}" if current_dir else name
        if name in entries:
            continue
        child_is_dir = "/" in relative_path or is_dir
        child_size = records.get(child_path, (child_is_dir, 0))[1]
        entries[name] = ArchiveEntry(
            name=name,
            member_path=child_path,
            is_dir=child_is_dir,
            size=child_size,
        )

    return sorted(
        entries.values(),
        key=lambda entry: (not entry.is_dir, entry.name.casefold()),
    )


def _selected_archive_roots(entries: list[ArchiveEntry]) -> list[str]:
    roots: list[str] = []
    for entry in sorted(entries, key=lambda item: item.member_path):
        if any(
            entry.member_path == root
            or entry.member_path.startswith(f"{root}/")
            for root in roots
        ):
            continue
        roots.append(entry.member_path)
    return roots


def _extract_selected_archive_members(
    archive_path: Path,
    password: str | None,
    selected_roots: list[str],
    records: dict[str, tuple[bool, int]],
    actual_members: set[str],
    destination: Path,
) -> None:
    selected_members = {
        member_path
        for member_path in records
        if any(
            member_path == root or member_path.startswith(f"{root}/")
            for root in selected_roots
        )
    }

    archive_type = _archive_format(archive_path)
    if archive_type == "zip":
        import pyzipper

        with pyzipper.AESZipFile(archive_path, mode="r") as archive:
            if password:
                archive.setpassword(password.encode("utf-8"))
            infos = {
                _normalize_member_name(info.filename): info
                for info in archive.infolist()
            }
            for member_path in selected_members:
                target = _safe_extract_path(destination, member_path)
                if records[member_path][0]:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                info = infos[member_path]
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, mode="r") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        return

    if archive_type in {"tar", "tar.gz", "tar.bz2"}:
        mode = {
            "tar": "r",
            "tar.gz": "r:gz",
            "tar.bz2": "r:bz2",
        }[archive_type]
        with tarfile.open(archive_path, mode=mode) as archive:
            members = {
                _normalize_member_name(member.name): member
                for member in archive.getmembers()
            }
            for member_path in selected_members:
                target = _safe_extract_path(destination, member_path)
                member = members.get(member_path)
                if member is None or member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        return

    import py7zr

    targets = sorted(actual_members & selected_members)
    with py7zr.SevenZipFile(
        archive_path,
        mode="r",
        password=password,
    ) as archive:
        archive.extract(path=destination, targets=targets)


def copy_archive_entries(
    archive_path: str | Path,
    entries: list[ArchiveEntry],
    destination: str | Path,
    *,
    password: str | None = None,
    overwrite: bool = False,
) -> int:
    archive_path = Path(archive_path).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if not destination.is_dir():
        raise NotADirectoryError(destination)
    if not entries:
        raise ValueError("コピーする項目がありません。")

    records, actual_members = _archive_records(archive_path, password)
    selected_roots = _selected_archive_roots(entries)
    if any(root not in records for root in selected_roots):
        raise FileNotFoundError("アーカイブ内の項目が見つかりません。")

    with TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        _extract_selected_archive_members(
            archive_path,
            password,
            selected_roots,
            records,
            actual_members,
            temporary_path,
        )
        for root in selected_roots:
            source = temporary_path / Path(root)
            target = destination / Path(root).name
            if target.exists():
                if not overwrite:
                    raise FileExistsError(target)
                _remove_existing(target)
            if source.is_dir():
                shutil.copytree(source, target)
            elif source.is_file():
                shutil.copy2(source, target)
    return len(selected_roots)


def _archive_members(source: Path):
    if not source.is_dir():
        yield source
        return

    yield source
    for child in sorted(
        source.rglob("*"),
        key=lambda path: str(path).casefold(),
    ):
        yield child


def _archive_name(source: Path, base_path: Path) -> str:
    try:
        return source.relative_to(base_path).as_posix()
    except ValueError as error:
        raise ValueError(
            f"対象が基準フォルダーの外にあります: {source}"
        ) from error


def _write_zip_members(archive, sources: list[Path], base_path: Path) -> None:
    for source in sources:
        for member in _archive_members(source):
            archive_name = _archive_name(member, base_path)
            if member.is_dir():
                archive.write(member, f"{archive_name}/")
            elif member.is_file():
                archive.write(member, archive_name)


def create_archive(
    archive_path: Path,
    base_path: Path,
    paths: list[Path],
    *,
    overwrite: bool = False,
    password: str | None = None,
) -> Path:
    archive_path = archive_path.expanduser().resolve()
    base_path = base_path.expanduser().resolve()
    sources = [path.expanduser().resolve() for path in paths]
    archive_type = _archive_format(archive_path)

    if password and archive_type in {"tar", "tar.gz", "tar.bz2"}:
        raise ArchivePasswordError(
            "tar形式ではパスワードを設定できません。.zipまたは.7zを使用してください。"
        )
    if not archive_path.parent.is_dir():
        raise FileNotFoundError(archive_path.parent)
    if not sources:
        raise ValueError("アーカイブに含める項目がありません。")
    if any(not path.exists() for path in sources):
        raise FileNotFoundError("アーカイブ対象が存在しません。")
    if any(
        archive_path == source
        or (source.is_dir() and source in archive_path.parents)
        for source in sources
    ):
        raise ValueError("作成するアーカイブを自身の中へ含めることはできません。")
    if archive_path.exists():
        if not overwrite:
            raise FileExistsError(archive_path)
        _remove_existing(archive_path)

    try:
        if archive_type == "zip":
            if password:
                try:
                    import pyzipper
                except ModuleNotFoundError as error:
                    raise ArchivePasswordError(
                        ".zipのパスワード設定にはpyzipperが必要です。"
                    ) from error

                with pyzipper.AESZipFile(
                    archive_path,
                    mode="w",
                    compression=pyzipper.ZIP_DEFLATED,
                    encryption=pyzipper.WZ_AES,
                ) as archive:
                    archive.setpassword(password.encode("utf-8"))
                    _write_zip_members(archive, sources, base_path)
            else:
                with zipfile.ZipFile(
                    archive_path,
                    mode="w",
                    compression=zipfile.ZIP_DEFLATED,
                ) as archive:
                    _write_zip_members(archive, sources, base_path)
        elif archive_type in {"tar", "tar.gz", "tar.bz2"}:
            mode = {
                "tar": "w",
                "tar.gz": "w:gz",
                "tar.bz2": "w:bz2",
            }[archive_type]
            with tarfile.open(archive_path, mode=mode) as archive:
                for source in sources:
                    archive.add(
                        source,
                        arcname=_archive_name(source, base_path),
                    )
        else:
            try:
                import py7zr
            except ModuleNotFoundError as error:
                raise ArchiveFormatError(
                    ".7z の作成には py7zr が必要です。"
                ) from error

            with py7zr.SevenZipFile(
                archive_path,
                mode="w",
                password=password,
                header_encryption=bool(password),
            ) as archive:
                for source in sources:
                    archive_name = _archive_name(source, base_path)
                    if source.is_dir():
                        archive.writeall(source, arcname=archive_name)
                    else:
                        archive.write(source, arcname=archive_name)
    except Exception:
        if archive_path.exists():
            _remove_existing(archive_path)
        raise

    return archive_path


def _safe_extract_path(destination: Path, member_name: str) -> Path:
    normalized_name = member_name.replace("\\", "/")
    if not normalized_name or normalized_name in {".", "./"}:
        return destination
    if normalized_name.startswith("/") or (
        len(normalized_name) >= 2 and normalized_name[1] == ":"
    ):
        raise ValueError(f"安全でないアーカイブパスです: {member_name}")

    parts = [
        part
        for part in normalized_name.split("/")
        if part not in {"", "."}
    ]
    if any(part == ".." or ":" in part for part in parts):
        raise ValueError(f"安全でないアーカイブパスです: {member_name}")

    target = (destination / Path(*parts)).resolve()
    try:
        target.relative_to(destination)
    except ValueError as error:
        raise ValueError(f"安全でないアーカイブパスです: {member_name}") from error
    return target


def _extract_zip(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path, mode="r") as archive:
        for info in archive.infolist():
            target = _safe_extract_path(destination, info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError(f"リンクを含むアーカイブは展開できません: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, mode="r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _extract_tar(archive_path: Path, destination: Path, archive_type: str) -> None:
    mode = {
        "tar": "r",
        "tar.gz": "r:gz",
        "tar.bz2": "r:bz2",
    }[archive_type]
    with tarfile.open(archive_path, mode=mode) as archive:
        for member in archive.getmembers():
            target = _safe_extract_path(destination, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.issym() or member.islnk():
                raise ValueError(f"リンクを含むアーカイブは展開できません: {member.name}")
            if not member.isfile():
                raise ValueError(f"対応していない項目です: {member.name}")

            source = archive.extractfile(member)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _extract_7z(archive_path: Path, destination: Path) -> None:
    try:
        import py7zr
    except ModuleNotFoundError as error:
        raise ArchiveFormatError(
            ".7z の展開には py7zr が必要です。"
        ) from error

    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        for member_name in archive.getnames():
            _safe_extract_path(destination, member_name)
        archive.extractall(path=destination)


def extract_archive(archive_path: Path, destination: Path) -> Path:
    archive_path = archive_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    archive_type = _archive_format(archive_path)

    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    if not destination.is_dir():
        raise NotADirectoryError(destination)

    if archive_type == "zip":
        _extract_zip(archive_path, destination)
    elif archive_type == "7z":
        _extract_7z(archive_path, destination)
    else:
        _extract_tar(archive_path, destination, archive_type)
    return archive_path


def _remove_existing(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def copy_paths(
    paths: list[Path], destination: Path, *, overwrite: bool = False
) -> None:
    destination = destination.resolve()

    for source in paths:
        target = destination / source.name
        if target.exists():
            if not overwrite:
                raise FileExistsError(target)
            _remove_existing(target)
        if source.is_file():
            shutil.copy2(source, target)
        elif source.is_dir():
            shutil.copytree(source, target)


def delete_paths(paths: list[Path]) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()


def create_directory(parent: Path, name: str) -> Path:
    directory = parent / name
    directory.mkdir()
    return directory


def rename_path(source: Path, new_name: str) -> Path:
    destination = source.with_name(new_name)
    source.rename(destination)
    return destination


def open_with_association(path: Path) -> None:
    os.startfile(str(path))


def move_paths(
    paths: list[Path], destination: Path, *, overwrite: bool = False
) -> None:
    destination = destination.resolve()

    for source in paths:
        target = destination / source.name
        if target.exists():
            if not overwrite:
                raise FileExistsError(target)
            _remove_existing(target)
        shutil.move(source, target)