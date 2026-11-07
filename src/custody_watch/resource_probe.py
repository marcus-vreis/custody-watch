"""Amostras do próprio processo; RSS/working set não é heap Python nem VRAM."""

from __future__ import annotations

import ctypes
import os
import sys
import tracemalloc
from pathlib import Path


def memory_snapshot() -> dict:
    heap = tracemalloc.get_traced_memory() if tracemalloc.is_tracing() else (None, None)
    result = {
        "python_traced_bytes": heap[0],
        "python_traced_peak_bytes": heap[1],
        "rss_bytes": None,
        "rss_peak_bytes": None,
        "rss_scope": "unavailable",
    }
    try:
        if sys.platform == "win32":
            # Contrato Win32: PROCESS_MEMORY_COUNTERS, GetProcessMemoryInfo.
            # WorkingSetSize inclui páginas compartilhadas e não mede VRAM.
            class Counters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_uint32), ("faults", ctypes.c_uint32)] + [
                    (name, ctypes.c_size_t)
                    for name in (
                        "peak",
                        "working",
                        "quota_peak_paged",
                        "quota_paged",
                        "quota_peak_nonpaged",
                        "quota_nonpaged",
                        "pagefile",
                        "peak_pagefile",
                    )
                ]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(Counters),
                ctypes.c_uint32,
            ]
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(
                kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            ):
                raise OSError("GetProcessMemoryInfo falhou")
            result.update(
                rss_bytes=counters.working,
                rss_peak_bytes=counters.peak,
                rss_scope="windows_working_set_shared_and_private",
            )
        elif sys.platform.startswith("linux"):
            resident = int(Path("/proc/self/statm").read_text().split()[1])
            result.update(
                rss_bytes=resident * os.sysconf("SC_PAGE_SIZE"),
                rss_scope="linux_proc_statm_resident",
            )
    except (OSError, ValueError, IndexError, AttributeError):
        result["rss_scope"] = "unavailable"
    return result
