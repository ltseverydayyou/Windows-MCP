from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import time
import winreg
from ctypes import wintypes
from pathlib import Path
from typing import Any

import psutil
from PIL import ImageGrab
from mcp.server import MCPServer
from mcp.server.mcpserver.utilities.types import Image as MCPImage
from mcp.types import ToolAnnotations
from pywinauto import Desktop

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "allowed_roots": ["%USERPROFILE%"],
    "allow_all_filesystem": False,
    "shell_enabled": True,
    "shell_timeout_seconds": 120,
    "max_file_bytes": 2_097_152,
    "max_output_chars": 120_000,
    "desktop_control_enabled": True,
    "ui_automation_enabled": True,
    "max_screenshot_pixels": 20_000_000,
}

def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        if isinstance(user_cfg, dict):
            cfg.update(user_cfg)
    return cfg

CONFIG = load_config()

def expand_path(raw: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(raw))
    return Path(expanded)

def allowed_roots() -> list[Path]:
    roots: list[Path] = []
    for raw in CONFIG.get("allowed_roots", []):
        p = expand_path(str(raw)).resolve(strict=False)
        roots.append(p)
    if not roots:
        roots = [Path.home().resolve(strict=False)]
    return roots

ROOTS = allowed_roots()

def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

def guard_path(raw: str, *, must_exist: bool = False) -> Path:
    p = expand_path(raw)
    if not p.is_absolute():
        p = ROOTS[0] / p
    p = p.resolve(strict=False)

    if not bool(CONFIG.get("allow_all_filesystem", False)):
        if not any(path_is_within(p, root) for root in ROOTS):
            raise PermissionError(
                f"Path is outside allowed_roots: {p}. "
                f"Allowed roots: {[str(x) for x in ROOTS]}"
            )

    if must_exist and not p.exists():
        raise FileNotFoundError(str(p))
    return p

def clamp_text(text: str) -> tuple[str, bool]:
    limit = int(CONFIG.get("max_output_chars", 120_000))
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[truncated]...", True

READ = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    open_world_hint=False,
    idempotent_hint=True,
)

WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    open_world_hint=False,
    idempotent_hint=False,
)

SHELL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    open_world_hint=True,
    idempotent_hint=False,
)

PROCESS_READ = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    open_world_hint=False,
    idempotent_hint=True,
)

PROCESS_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    open_world_hint=True,
    idempotent_hint=False,
)

mcp = MCPServer(
    "pc-mcp",
    version="2.0.0",
    title="Windows PC MCP",
    description="Windows filesystem, shell, screen, input, window, UI Automation, process, clipboard, application, and system tools.",
    instructions=(
        "Operate only on the user's Windows PC through the declared tools. "
        "Prefer read-only inspection and snapshots before changing files, processes, windows, or UI state. "
        "Use exact paths and window handles. Use UI Automation selectors when possible and pixel input only when needed. "
        "Treat PowerShell, deletion, overwrites, process termination, mouse/keyboard input, window closing, "
        "and external/network-capable commands as consequential actions."
    ),
)

@mcp.tool(
    title="Get system information",
    description="Inspect Windows, Python, CPU, memory, hostname, user, and configured MCP roots.",
    annotations=READ,
)
def get_system_info() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "hostname": platform.node(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER"),
        "cpu_logical_count": psutil.cpu_count(logical=True),
        "memory_total_bytes": vm.total,
        "memory_available_bytes": vm.available,
        "allowed_roots": [str(x) for x in ROOTS],
        "allow_all_filesystem": bool(CONFIG.get("allow_all_filesystem", False)),
        "shell_enabled": bool(CONFIG.get("shell_enabled", True)),
    }

@mcp.tool(
    title="List drives",
    description="List mounted Windows drives and filesystems.",
    annotations=READ,
)
def list_drives() -> list[dict[str, Any]]:
    items = []
    for p in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(p.mountpoint)
            usage_info = {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "percent": usage.percent,
            }
        except Exception:
            usage_info = {}
        items.append({
            "device": p.device,
            "mountpoint": p.mountpoint,
            "fstype": p.fstype,
            "opts": p.opts,
            **usage_info,
        })
    return items

@mcp.tool(
    title="List directory",
    description="List files and folders in a directory, including sizes and timestamps.",
    annotations=READ,
)
def list_directory(path: str = ".", max_entries: int = 500) -> dict[str, Any]:
    p = guard_path(path, must_exist=True)
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    max_entries = max(1, min(int(max_entries), 5000))
    rows = []
    for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:max_entries]:
        try:
            stat = entry.stat()
            size = stat.st_size
            modified = stat.st_mtime
        except OSError:
            size = None
            modified = None
        rows.append({
            "name": entry.name,
            "path": str(entry),
            "type": "directory" if entry.is_dir() else "file",
            "size_bytes": size,
            "modified_unix": modified,
        })
    return {"path": str(p), "entries": rows, "count": len(rows)}

@mcp.tool(
    title="Stat path",
    description="Inspect metadata for one file or directory.",
    annotations=READ,
)
def stat_path(path: str) -> dict[str, Any]:
    p = guard_path(path, must_exist=True)
    s = p.stat()
    return {
        "path": str(p),
        "name": p.name,
        "is_file": p.is_file(),
        "is_dir": p.is_dir(),
        "size_bytes": s.st_size,
        "created_unix": s.st_ctime,
        "modified_unix": s.st_mtime,
        "mode": s.st_mode,
    }

@mcp.tool(
    title="Read text file",
    description="Read a UTF-8 text file, optionally limited to a line range.",
    annotations=READ,
)
def read_text_file(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> dict[str, Any]:
    p = guard_path(path, must_exist=True)
    if not p.is_file():
        raise IsADirectoryError(str(p))

    max_bytes = int(CONFIG.get("max_file_bytes", 2_097_152))
    size = p.stat().st_size
    if size > max_bytes:
        raise ValueError(f"File is {size} bytes; configured max_file_bytes is {max_bytes}")

    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(start_line))
    end = len(lines) if end_line is None else max(start, min(int(end_line), len(lines)))
    selected = "\n".join(lines[start - 1:end])
    selected, truncated = clamp_text(selected)
    return {
        "path": str(p),
        "start_line": start,
        "end_line": end,
        "total_lines": len(lines),
        "text": selected,
        "truncated": truncated,
    }

@mcp.tool(
    title="Search text",
    description="Recursively search text files below a directory for a literal string.",
    annotations=READ,
)
def search_text(
    root: str,
    query: str,
    glob_pattern: str = "*",
    case_sensitive: bool = False,
    max_files: int = 2000,
    max_matches: int = 200,
) -> dict[str, Any]:
    base = guard_path(root, must_exist=True)
    if not base.is_dir():
        raise NotADirectoryError(str(base))

    max_files = max(1, min(int(max_files), 20_000))
    max_matches = max(1, min(int(max_matches), 5000))
    max_bytes = int(CONFIG.get("max_file_bytes", 2_097_152))
    needle = query if case_sensitive else query.lower()

    scanned = 0
    matches: list[dict[str, Any]] = []
    for file in base.rglob(glob_pattern):
        if scanned >= max_files or len(matches) >= max_matches:
            break
        if not file.is_file():
            continue
        try:
            if file.stat().st_size > max_bytes:
                continue
            scanned += 1
            with file.open("r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, 1):
                    hay = line if case_sensitive else line.lower()
                    if needle in hay:
                        excerpt, _ = clamp_text(line.rstrip("\r\n"))
                        matches.append({
                            "path": str(file),
                            "line": line_no,
                            "text": excerpt[:2000],
                        })
                        if len(matches) >= max_matches:
                            break
        except (OSError, UnicodeError):
            continue

    return {
        "root": str(base),
        "query": query,
        "files_scanned": scanned,
        "matches": matches,
        "match_count": len(matches),
    }

@mcp.tool(
    title="Write text file",
    description="Create or overwrite a UTF-8 text file. overwrite must be true to replace an existing file.",
    annotations=WRITE,
)
def write_text_file(
    path: str,
    content: str,
    overwrite: bool = False,
    create_parents: bool = True,
) -> dict[str, Any]:
    p = guard_path(path)
    if p.exists() and p.is_dir():
        raise IsADirectoryError(str(p))
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} already exists; set overwrite=true to replace it")
    if create_parents:
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="")
    return {"path": str(p), "bytes_written": p.stat().st_size}

@mcp.tool(
    title="Replace text in file",
    description="Replace exact text in a UTF-8 file. Useful for focused code edits.",
    annotations=WRITE,
)
def replace_in_file(
    path: str,
    old_text: str,
    new_text: str,
    max_replacements: int = 1,
) -> dict[str, Any]:
    p = guard_path(path, must_exist=True)
    if not p.is_file():
        raise IsADirectoryError(str(p))
    text = p.read_text(encoding="utf-8", errors="strict")
    found = text.count(old_text)
    if found == 0:
        raise ValueError("old_text was not found")
    limit = max(1, int(max_replacements))
    updated = text.replace(old_text, new_text, limit)
    p.write_text(updated, encoding="utf-8", newline="")
    return {
        "path": str(p),
        "occurrences_found": found,
        "replacements_made": min(found, limit),
    }

@mcp.tool(
    title="Move path",
    description="Move or rename a file or directory. Set overwrite=true only when replacement is intended.",
    annotations=WRITE,
)
def move_path(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
    src = guard_path(source, must_exist=True)
    dst = guard_path(destination)

    if dst.exists():
        if not overwrite:
            raise FileExistsError(str(dst))
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"source": str(src), "destination": str(dst)}

@mcp.tool(
    title="Delete path",
    description="Delete a file, or a directory when recursive=true. Configured allowed roots themselves cannot be deleted.",
    annotations=WRITE,
)
def delete_path(path: str, recursive: bool = False) -> dict[str, Any]:
    p = guard_path(path, must_exist=True)
    for root in ROOTS:
        if p == root:
            raise PermissionError("Refusing to delete a configured allowed root")

    if p.is_dir():
        if not recursive:
            p.rmdir()
        else:
            shutil.rmtree(p)
    else:
        p.unlink()
    return {"deleted": str(p)}

@mcp.tool(
    title="Run PowerShell",
    description="Run a PowerShell command locally and return stdout, stderr, exit code, and timeout status.",
    annotations=SHELL,
)
def run_powershell(
    command: str,
    cwd: str = "",
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    if not bool(CONFIG.get("shell_enabled", True)):
        raise PermissionError("PowerShell execution is disabled in config.json")

    workdir = guard_path(cwd or str(ROOTS[0]), must_exist=True)
    if not workdir.is_dir():
        raise NotADirectoryError(str(workdir))

    configured_timeout = int(CONFIG.get("shell_timeout_seconds", 120))
    timeout = configured_timeout if timeout_seconds is None else max(1, min(int(timeout_seconds), configured_timeout))

    try:
        cp = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=str(workdir),
            text=True,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        stdout, out_trunc = clamp_text(cp.stdout or "")
        stderr, err_trunc = clamp_text(cp.stderr or "")
        return {
            "cwd": str(workdir),
            "exit_code": cp.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": out_trunc or err_trunc,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        stdout, out_trunc = clamp_text(stdout)
        stderr, err_trunc = clamp_text(stderr)
        return {
            "cwd": str(workdir),
            "exit_code": None,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": out_trunc or err_trunc,
            "timed_out": True,
        }

@mcp.tool(
    title="List processes",
    description="List running processes with PID, name, executable path, and username when available.",
    annotations=PROCESS_READ,
)
def list_processes(name_filter: str = "", max_entries: int = 500) -> list[dict[str, Any]]:
    needle = name_filter.lower().strip()
    max_entries = max(1, min(int(max_entries), 5000))
    rows = []
    for proc in psutil.process_iter(["pid", "name", "exe", "username", "status"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            if needle and needle not in name.lower():
                continue
            rows.append(info)
            if len(rows) >= max_entries:
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return rows

@mcp.tool(
    title="Start process",
    description="Start a local executable with arguments. This does not invoke a shell.",
    annotations=PROCESS_WRITE,
)
def start_process(
    executable: str,
    arguments: list[str] | None = None,
    cwd: str = "",
) -> dict[str, Any]:
    args = [executable] + list(arguments or [])
    workdir = guard_path(cwd or str(ROOTS[0]), must_exist=True)
    proc = subprocess.Popen(
        args,
        cwd=str(workdir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return {"pid": proc.pid, "executable": executable, "arguments": list(arguments or []), "cwd": str(workdir)}

@mcp.tool(
    title="Stop process",
    description="Terminate a process by PID. Set force=true to kill it immediately.",
    annotations=PROCESS_WRITE,
)
def stop_process(pid: int, force: bool = False) -> dict[str, Any]:
    proc = psutil.Process(int(pid))
    name = proc.name()
    if force:
        proc.kill()
    else:
        proc.terminate()
    return {"pid": int(pid), "name": name, "force": bool(force)}

# ---- Desktop, window, input, UI Automation, and richer system tools ----

USER32 = ctypes.windll.user32
KERNEL32 = ctypes.windll.kernel32

USER32.GetForegroundWindow.restype = wintypes.HWND
USER32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
USER32.GetWindowTextLengthW.restype = ctypes.c_int
USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetWindowTextW.restype = ctypes.c_int
USER32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetClassNameW.restype = ctypes.c_int
USER32.IsWindowVisible.argtypes = [wintypes.HWND]
USER32.IsWindowVisible.restype = wintypes.BOOL
USER32.IsIconic.argtypes = [wintypes.HWND]
USER32.IsIconic.restype = wintypes.BOOL
USER32.IsZoomed.argtypes = [wintypes.HWND]
USER32.IsZoomed.restype = wintypes.BOOL
USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
USER32.GetWindowRect.restype = wintypes.BOOL
USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER32.GetWindowThreadProcessId.restype = wintypes.DWORD
USER32.SetForegroundWindow.argtypes = [wintypes.HWND]
USER32.SetForegroundWindow.restype = wintypes.BOOL
USER32.BringWindowToTop.argtypes = [wintypes.HWND]
USER32.BringWindowToTop.restype = wintypes.BOOL
USER32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
USER32.ShowWindow.restype = wintypes.BOOL
USER32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
USER32.MoveWindow.restype = wintypes.BOOL
USER32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.PostMessageW.restype = wintypes.BOOL
USER32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
USER32.SetCursorPos.restype = wintypes.BOOL
USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
USER32.GetCursorPos.restype = wintypes.BOOL

try:
    USER32.SetProcessDPIAware()
except Exception:
    pass

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_RESTORE = 9
WM_CLOSE = 0x0010
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
WM_CHAR = 0x0102
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
MONITORINFOF_PRIMARY = 0x00000001


def _desktop_control_guard() -> None:
    if not bool(CONFIG.get("desktop_control_enabled", True)):
        raise PermissionError("Desktop control is disabled in config.json")


def _ui_guard() -> None:
    if not bool(CONFIG.get("ui_automation_enabled", True)):
        raise PermissionError("UI Automation is disabled in config.json")


def _foreground_hwnd() -> int:
    return int(USER32.GetForegroundWindow() or 0)


def _window_text(hwnd: int) -> str:
    length = USER32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    buf = ctypes.create_unicode_buffer(max(1, length + 1))
    USER32.GetWindowTextW(wintypes.HWND(hwnd), buf, len(buf))
    return buf.value


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetClassNameW(wintypes.HWND(hwnd), buf, len(buf))
    return buf.value


def _window_rect(hwnd: int) -> dict[str, int]:
    rect = wintypes.RECT()
    if not USER32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise OSError(f"GetWindowRect failed for hwnd={hwnd}")
    return {
        "left": int(rect.left),
        "top": int(rect.top),
        "right": int(rect.right),
        "bottom": int(rect.bottom),
        "width": int(rect.right - rect.left),
        "height": int(rect.bottom - rect.top),
    }


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    USER32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _window_record(hwnd: int) -> dict[str, Any]:
    pid = _window_pid(hwnd)
    process_name = ""
    executable = ""
    try:
        proc = psutil.Process(pid)
        process_name = proc.name()
        try:
            executable = proc.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return {
        "hwnd": int(hwnd),
        "title": _window_text(hwnd),
        "class_name": _window_class(hwnd),
        "pid": pid,
        "process_name": process_name,
        "executable": executable,
        "visible": bool(USER32.IsWindowVisible(wintypes.HWND(hwnd))),
        "minimized": bool(USER32.IsIconic(wintypes.HWND(hwnd))),
        "maximized": bool(USER32.IsZoomed(wintypes.HWND(hwnd))),
        "foreground": int(hwnd) == _foreground_hwnd(),
        "rect": _window_rect(hwnd),
    }


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def _monitor_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def callback(hmonitor: int, _hdc: int, rect_ptr: Any, _lparam: int) -> bool:
        rect = rect_ptr.contents
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        device = ""
        work = rect
        primary = False
        if USER32.GetMonitorInfoW(ctypes.c_void_p(hmonitor), ctypes.byref(info)):
            device = info.szDevice
            work = info.rcWork
            primary = bool(info.dwFlags & MONITORINFOF_PRIMARY)
        records.append({
            "index": len(records),
            "handle": int(hmonitor or 0),
            "device": device,
            "primary": primary,
            "rect": {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
                "width": int(rect.right - rect.left),
                "height": int(rect.bottom - rect.top),
            },
            "work_area": {
                "left": int(work.left),
                "top": int(work.top),
                "right": int(work.right),
                "bottom": int(work.bottom),
                "width": int(work.right - work.left),
                "height": int(work.bottom - work.top),
            },
        })
        return True

    cb = callback_type(callback)
    USER32.EnumDisplayMonitors(None, None, cb, 0)
    return records


@mcp.tool(
    title="List monitors",
    description="List connected displays with desktop coordinates, work areas, and primary-display status.",
    annotations=READ,
)
def list_monitors() -> list[dict[str, Any]]:
    return _monitor_records()


@mcp.tool(
    title="Take screen snapshot",
    description="Capture the whole desktop, a monitor, the foreground window, or a specific window and return it as MCP image content.",
    annotations=READ,
    structured_output=False,
)
def take_screenshot(
    target: str = "desktop",
    hwnd: int = 0,
    monitor_index: int = 0,
    save_path: str = "",
    image_format: str = "png",
    jpeg_quality: int = 90,
) -> list[Any]:
    mode = target.strip().lower()
    fmt = image_format.strip().lower()
    if fmt not in {"png", "jpg", "jpeg", "webp"}:
        raise ValueError("image_format must be png, jpg, jpeg, or webp")

    bbox = None
    resolved_hwnd = 0
    if mode == "desktop":
        image = ImageGrab.grab(all_screens=True, include_layered_windows=True)
    elif mode == "monitor":
        monitors = _monitor_records()
        idx = int(monitor_index)
        if idx < 0 or idx >= len(monitors):
            raise IndexError(f"monitor_index must be between 0 and {max(0, len(monitors) - 1)}")
        rect = monitors[idx]["rect"]
        bbox = (rect["left"], rect["top"], rect["right"], rect["bottom"])
        image = ImageGrab.grab(bbox=bbox, include_layered_windows=True)
    elif mode in {"foreground", "window"}:
        resolved_hwnd = _foreground_hwnd() if mode == "foreground" and not hwnd else int(hwnd)
        if not resolved_hwnd:
            raise ValueError("No foreground window is available")
        rect = _window_rect(resolved_hwnd)
        bbox = (rect["left"], rect["top"], rect["right"], rect["bottom"])
        image = ImageGrab.grab(bbox=bbox, include_layered_windows=True)
    else:
        raise ValueError("target must be desktop, monitor, foreground, or window")

    max_pixels = int(CONFIG.get("max_screenshot_pixels", 20_000_000))
    if image.width * image.height > max_pixels:
        scale = (max_pixels / float(image.width * image.height)) ** 0.5
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))

    buffer = io.BytesIO()
    save_fmt = "JPEG" if fmt in {"jpg", "jpeg"} else fmt.upper()
    if save_fmt == "JPEG" and image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    kwargs: dict[str, Any] = {}
    if save_fmt == "JPEG":
        kwargs["quality"] = max(20, min(int(jpeg_quality), 100))
    image.save(buffer, format=save_fmt, **kwargs)
    data = buffer.getvalue()

    saved = ""
    if save_path:
        p = guard_path(save_path)
        if p.exists() and p.is_dir():
            raise IsADirectoryError(str(p))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        saved = str(p)

    point = wintypes.POINT()
    USER32.GetCursorPos(ctypes.byref(point))
    metadata = {
        "target": mode,
        "hwnd": resolved_hwnd or None,
        "bbox": bbox,
        "width": image.width,
        "height": image.height,
        "format": fmt,
        "bytes": len(data),
        "saved_path": saved or None,
        "cursor": {"x": int(point.x), "y": int(point.y)},
    }
    return [metadata, MCPImage(data=data, format="jpeg" if fmt in {"jpg", "jpeg"} else fmt)]


@mcp.tool(
    title="List open windows",
    description="List top-level Windows app windows with HWND, title, process, state, and screen rectangle.",
    annotations=READ,
)
def list_windows(
    visible_only: bool = True,
    title_filter: str = "",
    process_filter: str = "",
    max_entries: int = 300,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    title_needle = title_filter.strip().lower()
    process_needle = process_filter.strip().lower()
    limit = max(1, min(int(max_entries), 2000))
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        try:
            if visible_only and not USER32.IsWindowVisible(hwnd):
                return True
            title = _window_text(int(hwnd))
            if title_needle and title_needle not in title.lower():
                return True
            row = _window_record(int(hwnd))
            if process_needle and process_needle not in (row.get("process_name") or "").lower():
                return True
            if visible_only and not title and not row.get("process_name"):
                return True
            rows.append(row)
        except Exception:
            pass
        return len(rows) < limit

    cb = callback_type(callback)
    USER32.EnumWindows(cb, 0)
    return rows[:limit]


@mcp.tool(
    title="Get window information",
    description="Inspect one top-level window by HWND. Use hwnd=0 for the current foreground window.",
    annotations=READ,
)
def get_window_info(hwnd: int = 0) -> dict[str, Any]:
    handle = int(hwnd) or _foreground_hwnd()
    if not handle:
        raise ValueError("No window handle is available")
    return _window_record(handle)


@mcp.tool(
    title="Focus window",
    description="Restore and bring a window to the foreground by HWND.",
    annotations=PROCESS_WRITE,
)
def focus_window(hwnd: int) -> dict[str, Any]:
    _desktop_control_guard()
    raw = int(hwnd)
    handle = wintypes.HWND(raw)
    USER32.ShowWindow(handle, SW_RESTORE)
    pywinauto_focused = False
    try:
        Desktop(backend="uia").window(handle=raw).set_focus()
        pywinauto_focused = True
    except Exception:
        pass
    brought = bool(USER32.BringWindowToTop(handle))
    focused = bool(USER32.SetForegroundWindow(handle))
    time.sleep(0.05)
    is_foreground = _foreground_hwnd() == raw
    return {
        "hwnd": raw,
        "pywinauto_focus_requested": pywinauto_focused,
        "brought_to_top": brought,
        "foreground_requested": focused,
        "is_foreground": is_foreground,
        "window": _window_record(raw),
    }


@mcp.tool(
    title="Set window state",
    description="Minimize, maximize, restore, show, hide, or request close for a window by HWND.",
    annotations=PROCESS_WRITE,
)
def set_window_state(hwnd: int, action: str) -> dict[str, Any]:
    _desktop_control_guard()
    handle = wintypes.HWND(int(hwnd))
    act = action.strip().lower()
    states = {
        "minimize": SW_SHOWMINIMIZED,
        "maximize": SW_SHOWMAXIMIZED,
        "restore": SW_RESTORE,
        "show": SW_SHOWNORMAL,
        "hide": SW_HIDE,
    }
    if act == "close":
        ok = bool(USER32.PostMessageW(handle, WM_CLOSE, 0, 0))
        return {"hwnd": int(hwnd), "action": act, "requested": ok}
    if act not in states:
        raise ValueError("action must be minimize, maximize, restore, show, hide, or close")
    USER32.ShowWindow(handle, states[act])
    return {"hwnd": int(hwnd), "action": act, "window": _window_record(int(hwnd))}


@mcp.tool(
    title="Move or resize window",
    description="Move and resize a top-level window using screen coordinates.",
    annotations=PROCESS_WRITE,
)
def move_resize_window(hwnd: int, x: int, y: int, width: int, height: int, repaint: bool = True) -> dict[str, Any]:
    _desktop_control_guard()
    if int(width) < 1 or int(height) < 1:
        raise ValueError("width and height must be positive")
    ok = bool(USER32.MoveWindow(wintypes.HWND(int(hwnd)), int(x), int(y), int(width), int(height), bool(repaint)))
    if not ok:
        raise OSError(f"MoveWindow failed for hwnd={hwnd}")
    return _window_record(int(hwnd))


@mcp.tool(
    title="Get cursor position",
    description="Return the current mouse cursor position in virtual desktop coordinates.",
    annotations=READ,
)
def get_cursor_position() -> dict[str, int]:
    point = wintypes.POINT()
    if not USER32.GetCursorPos(ctypes.byref(point)):
        raise OSError("GetCursorPos failed")
    return {"x": int(point.x), "y": int(point.y)}


@mcp.tool(
    title="Move cursor",
    description="Move the mouse cursor to absolute virtual desktop coordinates.",
    annotations=PROCESS_WRITE,
)
def move_cursor(x: int, y: int) -> dict[str, int]:
    _desktop_control_guard()
    if not USER32.SetCursorPos(int(x), int(y)):
        raise OSError("SetCursorPos failed")
    return get_cursor_position()


def _mouse_button_flags(button: str) -> tuple[int, int]:
    value = button.strip().lower()
    mapping = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }
    if value not in mapping:
        raise ValueError("button must be left, right, or middle")
    return mapping[value]


@mcp.tool(
    title="Click mouse",
    description="Click a mouse button at the current cursor position or optional absolute coordinates.",
    annotations=PROCESS_WRITE,
)
def click_mouse(button: str = "left", x: int | None = None, y: int | None = None, clicks: int = 1, interval_ms: int = 80) -> dict[str, Any]:
    _desktop_control_guard()
    if x is not None or y is not None:
        if x is None or y is None:
            raise ValueError("x and y must be provided together")
        move_cursor(int(x), int(y))
    down, up = _mouse_button_flags(button)
    count = max(1, min(int(clicks), 20))
    for i in range(count):
        USER32.mouse_event(down, 0, 0, 0, 0)
        USER32.mouse_event(up, 0, 0, 0, 0)
        if i + 1 < count:
            time.sleep(max(0, min(int(interval_ms), 2000)) / 1000.0)
    return {"button": button.lower(), "clicks": count, "position": get_cursor_position()}


@mcp.tool(
    title="Scroll mouse",
    description="Scroll the mouse wheel. Positive notches scroll up; negative notches scroll down.",
    annotations=PROCESS_WRITE,
)
def scroll_mouse(notches: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    _desktop_control_guard()
    if x is not None or y is not None:
        if x is None or y is None:
            raise ValueError("x and y must be provided together")
        move_cursor(int(x), int(y))
    amount = max(-100, min(int(notches), 100))
    USER32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_int(amount * 120), 0)
    return {"notches": amount, "position": get_cursor_position()}


@mcp.tool(
    title="Drag mouse",
    description="Drag a mouse button from one screen coordinate to another.",
    annotations=PROCESS_WRITE,
)
def drag_mouse(start_x: int, start_y: int, end_x: int, end_y: int, button: str = "left", duration_ms: int = 300, steps: int = 20) -> dict[str, Any]:
    _desktop_control_guard()
    down, up = _mouse_button_flags(button)
    move_cursor(int(start_x), int(start_y))
    USER32.mouse_event(down, 0, 0, 0, 0)
    try:
        total_steps = max(1, min(int(steps), 200))
        duration = max(0, min(int(duration_ms), 10_000)) / 1000.0
        delay = duration / total_steps if total_steps else 0
        for i in range(1, total_steps + 1):
            t = i / total_steps
            nx = round(int(start_x) + (int(end_x) - int(start_x)) * t)
            ny = round(int(start_y) + (int(end_y) - int(start_y)) * t)
            USER32.SetCursorPos(nx, ny)
            if delay:
                time.sleep(delay)
    finally:
        USER32.mouse_event(up, 0, 0, 0, 0)
    return {"button": button.lower(), "position": get_cursor_position()}


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


USER32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
USER32.GetGUIThreadInfo.restype = wintypes.BOOL


def _focused_control_hwnd() -> int:
    top = _foreground_hwnd()
    if not top:
        return 0
    thread_id = USER32.GetWindowThreadProcessId(wintypes.HWND(top), None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if USER32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return int(info.hwndFocus or 0)
    return 0


def _post_wm_char_text(text: str, interval_ms: int = 0) -> int:
    focus_hwnd = _focused_control_hwnd()
    if not focus_hwnd:
        raise OSError("No focused child control is available for WM_CHAR text input")
    raw = text.encode("utf-16-le")
    delay = max(0, min(int(interval_ms), 2000)) / 1000.0
    sent_units = 0
    for i in range(0, len(raw), 2):
        unit = int.from_bytes(raw[i:i + 2], "little")
        if not USER32.PostMessageW(wintypes.HWND(focus_hwnd), WM_CHAR, unit, 1):
            raise OSError(f"PostMessageW(WM_CHAR) failed after {sent_units} UTF-16 units")
        sent_units += 1
        if delay:
            time.sleep(delay)
    return sent_units


VK_MAP = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11, "control": 0x11,
    "alt": 0x12, "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    "win": 0x5B, "windows": 0x5B, "apps": 0x5D, "numlock": 0x90, "scrolllock": 0x91,
}


def _resolve_vk(key: str) -> int:
    value = key.strip().lower()
    if value in VK_MAP:
        return VK_MAP[value]
    if value.startswith("f") and value[1:].isdigit():
        n = int(value[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
    if len(key) == 1:
        vk = USER32.VkKeyScanW(ord(key))
        if vk == -1:
            raise ValueError(f"Cannot resolve key: {key}")
        return int(vk) & 0xFF
    raise ValueError(f"Unknown key: {key}")


@mcp.tool(
    title="Press keyboard hotkey",
    description="Press a key or key combination such as ['ctrl','shift','s'] using Win32 virtual keys.",
    annotations=PROCESS_WRITE,
)
def press_hotkey(keys: list[str], presses: int = 1, interval_ms: int = 80) -> dict[str, Any]:
    _desktop_control_guard()
    if not keys:
        raise ValueError("keys cannot be empty")
    virtual_keys = [_resolve_vk(k) for k in keys]
    count = max(1, min(int(presses), 50))
    delay = max(0, min(int(interval_ms), 2000)) / 1000.0
    for press_index in range(count):
        for vk in virtual_keys:
            USER32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(virtual_keys):
            USER32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        if press_index + 1 < count and delay:
            time.sleep(delay)
    return {"keys": keys, "presses": count}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


def _unicode_inputs(code_units: list[int]) -> list[INPUT]:
    events: list[INPUT] = []
    for unit in code_units:
        events.append(INPUT(type=1, ki=KEYBDINPUT(0, int(unit), KEYEVENTF_UNICODE, 0, 0)))
        events.append(INPUT(type=1, ki=KEYBDINPUT(0, int(unit), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)))
    return events


def _send_input_events(events: list[INPUT]) -> None:
    if not events:
        return
    array_type = INPUT * len(events)
    array = array_type(*events)
    sent = USER32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise OSError(f"SendInput sent {sent}/{len(events)} events (size={ctypes.sizeof(INPUT)})")


@mcp.tool(
    title="Type text",
    description="Type literal Unicode text into the currently focused application.",
    annotations=PROCESS_WRITE,
)
def type_text(text: str, interval_ms: int = 0) -> dict[str, Any]:
    _desktop_control_guard()
    raw = text.encode("utf-16-le")
    code_units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    has_non_bmp = any(ord(ch) > 0xFFFF for ch in text)
    if has_non_bmp:
        sent_units = _post_wm_char_text(text, interval_ms=interval_ms)
        return {
            "utf16_units_typed": sent_units,
            "characters": len(text),
            "method": "wm_char",
        }

    delay = max(0, min(int(interval_ms), 2000)) / 1000.0
    if delay:
        for unit in code_units:
            _send_input_events(_unicode_inputs([unit]))
            time.sleep(delay)
    else:
        batch_units = 512
        for start in range(0, len(code_units), batch_units):
            _send_input_events(_unicode_inputs(code_units[start:start + batch_units]))
    return {
        "utf16_units_typed": len(code_units),
        "characters": len(text),
        "method": "send_input",
    }


KERNEL32.GlobalLock.restype = ctypes.c_void_p
KERNEL32.GlobalAlloc.restype = ctypes.c_void_p


@mcp.tool(
    title="Get clipboard text",
    description="Read Unicode text currently stored in the Windows clipboard.",
    annotations=READ,
)
def get_clipboard_text() -> dict[str, Any]:
    if not USER32.OpenClipboard(None):
        raise OSError("OpenClipboard failed")
    try:
        handle = USER32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return {"text": "", "has_unicode_text": False}
        ptr = KERNEL32.GlobalLock(handle)
        if not ptr:
            raise OSError("GlobalLock failed")
        try:
            text = ctypes.wstring_at(ptr)
        finally:
            KERNEL32.GlobalUnlock(handle)
        text, truncated = clamp_text(text)
        return {"text": text, "has_unicode_text": True, "truncated": truncated}
    finally:
        USER32.CloseClipboard()


@mcp.tool(
    title="Set clipboard text",
    description="Replace the Windows clipboard contents with Unicode text.",
    annotations=PROCESS_WRITE,
)
def set_clipboard_text(text: str) -> dict[str, Any]:
    _desktop_control_guard()
    data = (text + "\x00").encode("utf-16-le")
    handle = KERNEL32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise MemoryError("GlobalAlloc failed")
    ptr = KERNEL32.GlobalLock(handle)
    if not ptr:
        KERNEL32.GlobalFree(handle)
        raise OSError("GlobalLock failed")
    ctypes.memmove(ptr, data, len(data))
    KERNEL32.GlobalUnlock(handle)
    if not USER32.OpenClipboard(None):
        KERNEL32.GlobalFree(handle)
        raise OSError("OpenClipboard failed")
    try:
        if not USER32.EmptyClipboard():
            raise OSError("EmptyClipboard failed")
        if not USER32.SetClipboardData(CF_UNICODETEXT, handle):
            raise OSError("SetClipboardData failed")
        handle = None
    finally:
        USER32.CloseClipboard()
        if handle:
            KERNEL32.GlobalFree(handle)
    return {"characters": len(text)}


def _uia_root(hwnd: int):
    _ui_guard()
    handle = int(hwnd) or _foreground_hwnd()
    if not handle:
        raise ValueError("No window handle is available")
    return handle, Desktop(backend="uia").window(handle=handle).wrapper_object()


def _uia_row(wrapper: Any, depth: int) -> dict[str, Any]:
    info = wrapper.element_info
    try:
        rect = wrapper.rectangle()
        rect_data = {
            "left": int(rect.left), "top": int(rect.top), "right": int(rect.right), "bottom": int(rect.bottom),
            "width": int(rect.width()), "height": int(rect.height()),
        }
    except Exception:
        rect_data = None
    try:
        text = wrapper.window_text()
    except Exception:
        text = ""
    display_name = text or getattr(info, "name", "") or ""
    if len(display_name) > 8000:
        display_name = display_name[:8000] + "...[truncated]..."
    return {
        "depth": depth,
        "name": display_name,
        "control_type": getattr(info, "control_type", "") or "",
        "automation_id": getattr(info, "automation_id", "") or "",
        "class_name": getattr(info, "class_name", "") or "",
        "enabled": bool(wrapper.is_enabled()) if hasattr(wrapper, "is_enabled") else None,
        "visible": bool(wrapper.is_visible()) if hasattr(wrapper, "is_visible") else None,
        "rect": rect_data,
    }


@mcp.tool(
    title="Inspect window UI",
    description="Inspect the UI Automation control tree of a window, including names, control types, automation IDs, classes, and rectangles.",
    annotations=READ,
)
def inspect_window_ui(hwnd: int = 0, max_depth: int = 4, max_elements: int = 400) -> dict[str, Any]:
    handle, root = _uia_root(hwnd)
    depth_limit = max(0, min(int(max_depth), 20))
    element_limit = max(1, min(int(max_elements), 5000))
    rows: list[dict[str, Any]] = []
    stack: list[tuple[Any, int]] = [(root, 0)]
    while stack and len(rows) < element_limit:
        wrapper, depth = stack.pop()
        try:
            rows.append(_uia_row(wrapper, depth))
            if depth < depth_limit:
                children = wrapper.children()
                for child in reversed(children):
                    stack.append((child, depth + 1))
        except Exception as exc:
            rows.append({"depth": depth, "error": str(exc)})
    return {"hwnd": handle, "window": _window_record(handle), "elements": rows, "count": len(rows), "truncated": bool(stack)}


def _find_uia_matches(root: Any, name: str, control_type: str, automation_id: str, max_matches: int) -> list[Any]:
    name_needle = name.strip().lower()
    type_needle = control_type.strip().lower()
    id_needle = automation_id.strip().lower()
    wrappers = [root]
    try:
        wrappers.extend(root.descendants())
    except Exception:
        pass
    matches = []
    for wrapper in wrappers:
        try:
            info = wrapper.element_info
            item_name = (wrapper.window_text() or getattr(info, "name", "") or "").lower()
            item_type = (getattr(info, "control_type", "") or "").lower()
            item_id = (getattr(info, "automation_id", "") or "").lower()
            if name_needle and name_needle not in item_name:
                continue
            if type_needle and type_needle != item_type:
                continue
            if id_needle and id_needle != item_id:
                continue
            matches.append(wrapper)
            if len(matches) >= max_matches:
                break
        except Exception:
            continue
    return matches


@mcp.tool(
    title="Find UI elements",
    description="Find UI Automation controls inside a window by name substring, control type, and/or exact automation ID.",
    annotations=READ,
)
def find_ui_elements(
    hwnd: int = 0,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    max_matches: int = 50,
) -> dict[str, Any]:
    if not any([name.strip(), control_type.strip(), automation_id.strip()]):
        raise ValueError("Provide name, control_type, or automation_id")
    handle, root = _uia_root(hwnd)
    limit = max(1, min(int(max_matches), 500))
    matches = _find_uia_matches(root, name, control_type, automation_id, limit)
    return {"hwnd": handle, "matches": [_uia_row(w, 0) for w in matches], "count": len(matches)}


@mcp.tool(
    title="Act on UI element",
    description="Perform click, double_click, right_click, focus, invoke, toggle, select, or set_text on a UI Automation element selected by name/type/automation ID.",
    annotations=PROCESS_WRITE,
)
def act_on_ui_element(
    hwnd: int = 0,
    action: str = "click",
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    index: int = 0,
    value: str = "",
) -> dict[str, Any]:
    _desktop_control_guard()
    if not any([name.strip(), control_type.strip(), automation_id.strip()]):
        raise ValueError("Provide name, control_type, or automation_id")
    handle, root = _uia_root(hwnd)
    matches = _find_uia_matches(root, name, control_type, automation_id, 200)
    idx = int(index)
    if idx < 0 or idx >= len(matches):
        raise IndexError(f"index {idx} is out of range for {len(matches)} match(es)")
    wrapper = matches[idx]
    act = action.strip().lower()
    if act == "click":
        wrapper.click_input()
    elif act == "double_click":
        wrapper.double_click_input()
    elif act == "right_click":
        wrapper.click_input(button="right")
    elif act == "focus":
        wrapper.set_focus()
    elif act == "invoke":
        wrapper.invoke()
    elif act == "toggle":
        wrapper.toggle()
    elif act == "select":
        wrapper.select()
    elif act == "set_text":
        set_ok = False
        if hasattr(wrapper, "set_edit_text"):
            try:
                wrapper.set_edit_text(value)
                set_ok = True
            except Exception:
                pass
        if not set_ok and hasattr(wrapper, "set_text"):
            try:
                wrapper.set_text(value)
                set_ok = True
            except Exception:
                pass
        if not set_ok:
            try:
                wrapper.iface_value.SetValue(value)
                set_ok = True
            except Exception:
                pass
        if not set_ok:
            raise TypeError("Selected UI element does not expose a writable UI Automation value")
    else:
        raise ValueError("action must be click, double_click, right_click, focus, invoke, toggle, select, or set_text")
    return {"hwnd": handle, "action": act, "selected_index": idx, "element": _uia_row(wrapper, 0), "match_count": len(matches)}


@mcp.tool(
    title="Read UI element text",
    description="Read text/value content from a UI Automation element selected by name/type/automation ID.",
    annotations=READ,
)
def read_ui_element_text(
    hwnd: int = 0,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
    index: int = 0,
) -> dict[str, Any]:
    if not any([name.strip(), control_type.strip(), automation_id.strip()]):
        raise ValueError("Provide name, control_type, or automation_id")
    handle, root = _uia_root(hwnd)
    matches = _find_uia_matches(root, name, control_type, automation_id, 200)
    idx = int(index)
    if idx < 0 or idx >= len(matches):
        raise IndexError(f"index {idx} is out of range for {len(matches)} match(es)")
    wrapper = matches[idx]
    candidates: list[tuple[str, str]] = []
    try:
        candidates.append(("value_pattern", str(wrapper.iface_value.CurrentValue)))
    except Exception:
        pass
    try:
        candidates.append(("text_pattern", str(wrapper.iface_text.DocumentRange.GetText(-1))))
    except Exception:
        pass
    try:
        candidates.append(("window_text", str(wrapper.window_text())))
    except Exception:
        pass
    text = ""
    source = ""
    for candidate_source, candidate_text in candidates:
        if candidate_text:
            source = candidate_source
            text = candidate_text
            break
    text, truncated = clamp_text(text)
    return {
        "hwnd": handle,
        "selected_index": idx,
        "match_count": len(matches),
        "source": source or None,
        "text": text,
        "truncated": truncated,
        "element": _uia_row(wrapper, 0),
    }


@mcp.tool(
    title="Get process details",
    description="Inspect one process including command line, working directory, resource use, open files, and internet sockets when accessible.",
    annotations=PROCESS_READ,
)
def get_process_details(pid: int) -> dict[str, Any]:
    proc = psutil.Process(int(pid))
    with proc.oneshot():
        result = proc.as_dict(attrs=[
            "pid", "ppid", "name", "exe", "username", "status", "create_time", "cmdline", "cwd",
            "cpu_times", "memory_info", "num_threads",
        ], ad_value=None)
    try:
        result["open_files"] = [f.path for f in proc.open_files()[:200]]
    except (psutil.AccessDenied, psutil.NoSuchProcess, NotImplementedError):
        result["open_files"] = None
    try:
        connections = []
        for c in proc.net_connections(kind="inet")[:200]:
            connections.append({
                "fd": c.fd,
                "family": str(c.family),
                "type": str(c.type),
                "local": list(c.laddr) if c.laddr else None,
                "remote": list(c.raddr) if c.raddr else None,
                "status": c.status,
            })
        result["network_connections"] = connections
    except (psutil.AccessDenied, psutil.NoSuchProcess, NotImplementedError):
        result["network_connections"] = None
    return result


@mcp.tool(
    title="Get system resource snapshot",
    description="Inspect current CPU, memory, swap, disk, boot time, battery, and network I/O statistics.",
    annotations=READ,
)
def get_resource_snapshot() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    net = psutil.net_io_counters()
    battery = psutil.sensors_battery()
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device, "mountpoint": part.mountpoint, "fstype": part.fstype,
                "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free, "percent": usage.percent,
            })
        except Exception:
            continue
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.15),
        "cpu_per_core_percent": psutil.cpu_percent(interval=0.15, percpu=True),
        "memory": vm._asdict(),
        "swap": swap._asdict(),
        "boot_time_unix": psutil.boot_time(),
        "battery": battery._asdict() if battery else None,
        "network_io": net._asdict() if net else None,
        "disks": disks,
    }


@mcp.tool(
    title="List installed applications",
    description="List applications registered in Windows uninstall registry keys, optionally filtered by display name.",
    annotations=READ,
)
def list_installed_apps(name_filter: str = "", max_entries: int = 1000) -> list[dict[str, Any]]:
    needle = name_filter.strip().lower()
    limit = max(1, min(int(max_entries), 5000))
    locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", 0, "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY, "HKLM64"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY, "HKLM32"),
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for hive, key_path, view, source in locations:
        try:
            root = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            index = 0
            while len(rows) < limit:
                try:
                    sub_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, sub_name) as sub:
                        def q(name: str) -> str:
                            try:
                                return str(winreg.QueryValueEx(sub, name)[0])
                            except OSError:
                                return ""
                        display_name = q("DisplayName")
                        if not display_name or (needle and needle not in display_name.lower()):
                            continue
                        version = q("DisplayVersion")
                        dedupe = (display_name.lower(), version.lower())
                        if dedupe in seen:
                            continue
                        seen.add(dedupe)
                        rows.append({
                            "name": display_name,
                            "version": version,
                            "publisher": q("Publisher"),
                            "install_location": q("InstallLocation"),
                            "install_date": q("InstallDate"),
                            "uninstall_string": q("UninstallString"),
                            "source": source,
                        })
                except OSError:
                    continue
    return sorted(rows, key=lambda x: x["name"].lower())[:limit]


@mcp.tool(
    title="List Windows services",
    description="List Windows services with status, start type, account, and binary path when accessible.",
    annotations=READ,
)
def list_windows_services(name_filter: str = "", max_entries: int = 1000) -> list[dict[str, Any]]:
    needle = name_filter.strip().lower()
    limit = max(1, min(int(max_entries), 5000))
    rows: list[dict[str, Any]] = []
    for service in psutil.win_service_iter():
        if len(rows) >= limit:
            break
        try:
            info = service.as_dict()
            hay = f"{info.get('name', '')} {info.get('display_name', '')}".lower()
            if needle and needle not in hay:
                continue
            rows.append(info)
        except (psutil.AccessDenied, OSError):
            continue
    return rows


@mcp.tool(
    title="Hash file",
    description="Compute a cryptographic hash for a file inside the configured filesystem roots.",
    annotations=READ,
)
def hash_file(path: str, algorithm: str = "sha256") -> dict[str, Any]:
    p = guard_path(path, must_exist=True)
    if not p.is_file():
        raise IsADirectoryError(str(p))
    algo = algorithm.strip().lower()
    try:
        digest = hashlib.new(algo)
    except ValueError as exc:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}") from exc
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(p), "algorithm": algo, "digest": digest.hexdigest(), "size_bytes": p.stat().st_size}


if __name__ == "__main__":
    mcp.run(transport="stdio")