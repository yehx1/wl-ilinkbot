from __future__ import annotations

import base64
import io
import ctypes
from ctypes import wintypes
from PIL import ImageGrab


class DesktopError(RuntimeError):
    pass


def monitor_rects() -> list[tuple[int, int, int, int]]:
    if not hasattr(ctypes, "windll"):
        return []
    user32 = ctypes.windll.user32
    rects: list[tuple[int, int, int, int]] = []
    monitor_enum = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def visit(_monitor, _dc, rect_ptr, _data):
        rect = rect_ptr.contents
        rects.append((rect.left, rect.top, rect.right, rect.bottom))
        return True
    user32.EnumDisplayMonitors(0, 0, monitor_enum(visit), 0)
    return rects


def screenshot(screen_index: int | None = None) -> tuple[bytes, str]:
    """Capture the current virtual desktop; works only on the local Windows session."""
    try:
        if screen_index is None:
            image = ImageGrab.grab(all_screens=True)
        else:
            rects = monitor_rects()
            if screen_index < 0 or screen_index >= len(rects):
                raise DesktopError(f"屏幕序号无效，可用范围：0-{max(len(rects) - 1, 0)}")
            image = ImageGrab.grab(bbox=rects[screen_index], all_screens=True)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue(), "image/png"
    except Exception as exc:
        raise DesktopError(f"无法获取屏幕截图：{type(exc).__name__}") from exc


def _window(title: str) -> wintypes.HWND | None:
    user32 = ctypes.windll.user32
    found: list[wintypes.HWND] = []
    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, text, length + 1)
        if title.casefold() in text.value.casefold():
            found.append(hwnd)
            return False
        return True
    user32.EnumWindows(callback(visit), 0)
    return found[0] if found else None


def focus_window(title: str, maximize: bool = True) -> str:
    if not title.strip():
        raise DesktopError("窗口标题不能为空")
    if not hasattr(ctypes, "windll"):
        raise DesktopError("电脑控制仅支持 Windows")
    hwnd = _window(title.strip())
    if not hwnd:
        raise DesktopError(f"未找到窗口：{title}")
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 3 if maximize else 9)  # SW_MAXIMIZE / SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return f"已切换到窗口：{title}，{'已最大化' if maximize else '已恢复窗口'}"


def list_windows() -> list[str]:
    if not hasattr(ctypes, "windll"):
        raise DesktopError("电脑控制仅支持 Windows")
    user32 = ctypes.windll.user32
    titles: list[str] = []
    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            text = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, text, length + 1)
            value = text.value.strip()
            if value and value not in titles:
                titles.append(value)
        return True
    user32.EnumWindows(callback(visit), 0)
    return titles
