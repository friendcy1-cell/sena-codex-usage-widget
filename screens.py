"""Desktop work areas in the same system-DPI coordinate space as Tk."""
import ctypes
from ctypes import wintypes
import os


def work_areas(root):
    areas = []
    if os.name == "nt":
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE,
                                          wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        user = ctypes.windll.user32
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user.GetMonitorInfoW.restype = wintypes.BOOL
        user.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                             callback_type, wintypes.LPARAM]
        user.EnumDisplayMonitors.restype = wintypes.BOOL

        def collect(handle, dc, rect, data):
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            if user.GetMonitorInfoW(handle, ctypes.byref(info)):
                w = info.work
                if w.right > w.left and w.bottom > w.top:
                    areas.append((w.left, w.top, w.right, w.bottom))
            return True

        callback = callback_type(collect)
        user.EnumDisplayMonitors(None, None, callback, 0)
    return areas or [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]


def nearest_area(areas, x, y):
    def distance(a):
        left, top, right, bottom = a
        dx = max(left - x, 0, x - (right - 1))
        dy = max(top - y, 0, y - (bottom - 1))
        return dx * dx + dy * dy
    return min(areas, key=distance)


def clamp(area, x, y, width, height):
    left, top, right, bottom = area
    return (max(left, min(x, max(left, right - width))),
            max(top, min(y, max(top, bottom - height))))


def virtual_bounds(areas):
    return (min(a[0] for a in areas), min(a[1] for a in areas),
            max(a[2] for a in areas), max(a[3] for a in areas))


def geometry(x, y, width=None, height=None):
    # Tk '+-1920' means absolute negative X; '-1920' alone means right-edge offset.
    size = f"{width}x{height}" if width is not None else ""
    return f"{size}+{int(x)}+{int(y)}"
