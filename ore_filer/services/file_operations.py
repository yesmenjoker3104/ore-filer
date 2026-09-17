from pathlib import Path
import shutil


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
        source.rename(target)