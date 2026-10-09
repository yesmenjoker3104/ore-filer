"""ore-filer の認証情報管理。Windows 資格情報マネージャーを使用する。

サービス名は必ず "ore-filer/{category}" 形式にする。
これにより資格情報マネージャーで "ore-filer" で検索するだけで
ore-filer が管理するエントリをすべて確認・削除できる。

カテゴリ例:
  "git"   — HTTP/HTTPS リモートのユーザー名 / パスワード (トークン)
"""
from __future__ import annotations

_PREFIX = "ore-filer"

try:
    import keyring as _keyring
    import keyring.errors as _keyring_errors
    _available = True
except ImportError:
    _keyring = None  # type: ignore[assignment]
    _keyring_errors = None  # type: ignore[assignment]
    _available = False


def is_available() -> bool:
    return _available


def _service(category: str) -> str:
    return f"{_PREFIX}/{category}"


def save(category: str, key: str, username: str, password: str) -> None:
    """認証情報を保存する。

    Args:
        category: 用途を表す文字列（例: "git"）
        key:      対象を特定するキー（例: リモートURL）
        username: ユーザー名
        password: パスワードまたはトークン
    """
    if not _available:
        return
    service = _service(category)
    credential_key = f"{username}@{key}" if username else key
    _keyring.set_password(service, credential_key, password)
    # ユーザー名は credential_key 自体に含めるが、単体でも引けるように保存する
    _keyring.set_password(f"{service}:username", key, username)


def load(category: str, key: str) -> tuple[str, str] | None:
    """保存済みの認証情報を返す。未保存なら None。

    Returns:
        (username, password) のタプル、または None
    """
    if not _available:
        return None
    try:
        service = _service(category)
        username = _keyring.get_password(f"{service}:username", key) or ""
        credential_key = f"{username}@{key}" if username else key
        password = _keyring.get_password(service, credential_key)
        if password is None:
            return None
        return username, password
    except Exception:
        return None


def delete(category: str, key: str) -> None:
    """指定キーの認証情報を削除する。存在しなくてもエラーにしない。"""
    if not _available:
        return
    try:
        service = _service(category)
        username = _keyring.get_password(f"{service}:username", key) or ""
        credential_key = f"{username}@{key}" if username else key
        try:
            _keyring.delete_password(service, credential_key)
        except Exception:
            pass
        try:
            _keyring.delete_password(f"{service}:username", key)
        except Exception:
            pass
    except Exception:
        pass
