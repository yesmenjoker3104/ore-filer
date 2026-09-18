"""Windows Shell context menu via ctypes COM (no pywin32 required)."""
from __future__ import annotations
import ctypes
import ctypes.wintypes as wt
from pathlib import Path

_HRESULT = ctypes.c_long


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD), ("Data2", wt.WORD),
        ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(s: str) -> _GUID:
    import uuid
    b = uuid.UUID(s).bytes_le
    g = _GUID()
    ctypes.memmove(ctypes.addressof(g), b, 16)
    return g


_IID_IContextMenu = _guid("000214E4-0000-0000-C000-000000000046")
_IID_IShellFolder = _guid("000214E6-0000-0000-C000-000000000046")


class _CMINVOKECOMMANDINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("fMask", wt.DWORD), ("hwnd", wt.HWND),
        ("lpVerb", ctypes.c_char_p), ("lpParameters", ctypes.c_char_p),
        ("lpDirectory", ctypes.c_char_p), ("nShow", ctypes.c_int),
        ("dwHotKey", wt.DWORD), ("hIcon", wt.HANDLE),
    ]


def _com(ptr: int, idx: int, restype, *argtypes):
    """Return a callable for COM vtable method at vtable index idx."""
    vt = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))
    fp = ctypes.cast(vt[0], ctypes.POINTER(ctypes.c_void_p))[idx]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(fp)


def _release(ptr: int | None) -> None:
    if ptr:
        _com(ptr, 2, wt.ULONG)(ptr)  # IUnknown::Release


def show_shell_context_menu(paths: list[Path], hwnd: int) -> None:
    """Show Windows shell context menu for the given paths."""
    if not paths:
        return
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    try:
        _run(paths, hwnd)
    except Exception:
        pass
    finally:
        ole32.CoUninitialize()


def _run(paths: list[Path], hwnd: int) -> None:
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32

    # Group by parent directory; use first group only
    parent = paths[0].parent
    children = [p for p in paths if p.parent == parent] or [paths[0]]

    # Parse absolute PIDL for parent directory
    shell32.SHParseDisplayName.restype = _HRESULT
    shell32.SHParseDisplayName.argtypes = [
        ctypes.c_wchar_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), wt.ULONG, ctypes.POINTER(wt.ULONG),
    ]
    shell32.ILFree.restype = None
    shell32.ILFree.argtypes = [ctypes.c_void_p]

    par_pidl = ctypes.c_void_p()
    sfgao = wt.ULONG(0)
    if shell32.SHParseDisplayName(str(parent), None, ctypes.byref(par_pidl), 0, ctypes.byref(sfgao)) < 0:
        return

    folder: int | None = None
    try:
        shell32.SHGetDesktopFolder.restype = _HRESULT
        shell32.SHGetDesktopFolder.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        desktop_p = ctypes.c_void_p()
        if shell32.SHGetDesktopFolder(ctypes.byref(desktop_p)) < 0:
            return
        desktop = desktop_p.value
        try:
            # IShellFolder::BindToObject(pidl, NULL, IID_IShellFolder, &folder)
            folder_p = ctypes.c_void_p()
            hr = _com(desktop, 5, _HRESULT,
                      ctypes.c_void_p, ctypes.c_void_p,
                      ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
                desktop, par_pidl, None,
                ctypes.byref(_IID_IShellFolder), ctypes.byref(folder_p),
            )
            if hr < 0:
                return
            folder = folder_p.value
        finally:
            _release(desktop)
    finally:
        shell32.ILFree(par_pidl)

    if not folder:
        return

    child_pidls: list[int] = []
    ctx: int | None = None
    try:
        # IShellFolder::ParseDisplayName for each child name → relative PIDL
        for child in children:
            cp = ctypes.c_void_p()
            eaten = wt.ULONG(0)
            attrs = wt.ULONG(0)
            hr = _com(folder, 3, _HRESULT,
                      wt.HWND, ctypes.c_void_p, ctypes.c_wchar_p,
                      ctypes.POINTER(wt.ULONG),
                      ctypes.POINTER(ctypes.c_void_p),
                      ctypes.POINTER(wt.ULONG))(
                folder, hwnd, None, child.name,
                ctypes.byref(eaten), ctypes.byref(cp), ctypes.byref(attrs),
            )
            if hr >= 0 and cp.value:
                child_pidls.append(cp.value)

        if not child_pidls:
            return

        # IShellFolder::GetUIObjectOf → IContextMenu
        n = len(child_pidls)
        arr = (ctypes.c_void_p * n)(*child_pidls)
        ctx_p = ctypes.c_void_p()
        rsv = wt.UINT(0)
        hr = _com(folder, 10, _HRESULT,
                  wt.HWND, wt.UINT, ctypes.c_void_p,
                  ctypes.POINTER(_GUID), ctypes.POINTER(wt.UINT),
                  ctypes.POINTER(ctypes.c_void_p))(
            folder, hwnd, n,
            ctypes.cast(arr, ctypes.c_void_p),
            ctypes.byref(_IID_IContextMenu),
            ctypes.byref(rsv),
            ctypes.byref(ctx_p),
        )
        if hr < 0 or not ctx_p.value:
            return
        ctx = ctx_p.value

        # Build and show popup menu
        user32.CreatePopupMenu.restype = ctypes.c_void_p
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.TrackPopupMenu.argtypes = [
            ctypes.c_void_p, wt.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ]
        user32.DestroyMenu.argtypes = [ctypes.c_void_p]

        hmenu = user32.CreatePopupMenu()
        try:
            # IContextMenu::QueryContextMenu(hmenu, 0, idCmdFirst=1, idCmdLast, CMF_NORMAL)
            _com(ctx, 3, _HRESULT, ctypes.c_void_p, wt.UINT, wt.UINT, wt.UINT, wt.UINT)(
                ctx, hmenu, 0, 1, 0x7FFF, 0,
            )
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(hwnd)
            # TPM_RETURNCMD | TPM_RIGHTBUTTON = 0x0102
            cmd = user32.TrackPopupMenu(hmenu, 0x0102, pt.x, pt.y, 0, hwnd, None)
            if cmd > 0:
                ici = _CMINVOKECOMMANDINFO()
                ici.cbSize = ctypes.sizeof(_CMINVOKECOMMANDINFO)
                ici.hwnd = hwnd
                # MAKEINTRESOURCE(cmd - 1): cast integer to char pointer
                ici.lpVerb = ctypes.cast(cmd - 1, ctypes.c_char_p)
                ici.nShow = 1  # SW_NORMAL
                _com(ctx, 4, _HRESULT, ctypes.POINTER(_CMINVOKECOMMANDINFO))(
                    ctx, ctypes.byref(ici),
                )
        finally:
            user32.DestroyMenu(hmenu)
    finally:
        for pidl in child_pidls:
            shell32.ILFree(pidl)
        if ctx:
            _release(ctx)
        _release(folder)
