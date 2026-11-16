"""运行环境信息：峰值内存、平台、CPU、Python 版本。

只给基准报告用，不影响业务逻辑。峰值内存拿不到时返回 ``None``，报告里显示为「—」，
不因为一个平台没有实现就让整次基准失败。
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def machine_info() -> dict[str, str | int]:
    """这次基准跑在什么机器上：报告要能和别人的结果区分开。"""
    return {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count() or 1,
        "python": platform.python_version(),
        "processor": platform.processor() or "unknown",
    }


def peak_rss_mb() -> float | None:
    """当前进程到现在的峰值常驻内存（MB）；拿不到时返回 ``None``。"""
    try:
        if sys.platform == "win32":
            return _windows_peak_rss_mb()
        if sys.platform == "darwin":
            return _darwin_peak_rss_mb()
        return _linux_peak_rss_mb()
    except (OSError, ValueError, AttributeError, RuntimeError):
        return None


def _linux_peak_rss_mb() -> float | None:
    """``/proc/self/status`` 里的 ``VmHWM`` 是内核记录的峰值（kB）。"""
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024, 1)
    return None


def _darwin_peak_rss_mb() -> float | None:
    """macOS 的 ``ru_maxrss`` 单位是字节。"""
    import resource

    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 1)


def _windows_peak_rss_mb() -> float | None:
    """Windows 用 ``GetProcessMemoryInfo`` 取 ``PeakWorkingSetSize``（字节）。"""
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    process = kernel32.GetCurrentProcess()
    ok = psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
    if not ok:
        return None
    return round(counters.PeakWorkingSetSize / (1024 * 1024), 1)
