"""
widgets/driver_install.py
─────────────────────────
Installs the Silicon Labs CP210x USB-to-UART driver that ships inside the
build, so a customer does not have to find it on the web themselves.

The CubeSat kit talks to the PC through a CP2102, which Windows only exposes
as a COM port once this driver is present. Windows 10 and 11 usually fetch it
from Windows Update within a minute of plugging the board in, so this is the
fallback for a machine that is offline, has Windows Update driver search
turned off, or is in a lab with no internet — which describes a good number of
the college benches this kit sits on.

The driver files are the unmodified Silicon Labs package under
drivers/cp210x/, catalog and all. They are installed with pnputil, which needs
administrator rights, so clicking Install raises one UAC prompt. No part of
this runs unless the customer asks for it.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

# ── Where the bundled package lives, frozen or not ───────────────────────────
if getattr(sys, "frozen", False):
    BASE_PATH = sys._MEIPASS
else:
    BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

DRIVER_DIR = os.path.join(BASE_PATH, "drivers", "cp210x")
INF_PATH   = os.path.join(DRIVER_DIR, "silabser.inf")
LICENCE    = os.path.join(DRIVER_DIR, "SLAB_License_Agreement_VCP_Windows.txt")

# The CP2102 on the kit. Used only to describe what is being installed.
DEVICE_NAME = "Silicon Labs CP210x USB to UART Bridge"


def is_available() -> bool:
    """True if the driver package is present in this build."""
    return os.name == "nt" and os.path.isfile(INF_PATH)


def is_installed() -> bool:
    """
    True if the CP210x driver is already on this machine.

    Reads the service key the driver creates, which any user can do; the
    obvious alternative, `pnputil /enum-drivers`, needs administrator rights
    and so cannot be used just to decide whether to offer the button.
    """
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Services\silabser"):
            return True
    except OSError:
        return False


def licence_text() -> str:
    """The Silicon Labs licence that covers the bundled driver."""
    try:
        with open(LICENCE, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# ── Elevated pnputil ─────────────────────────────────────────────────────────

class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize",       wintypes.DWORD),
        ("fMask",        wintypes.ULONG),
        ("hwnd",         wintypes.HWND),
        ("lpVerb",       wintypes.LPCWSTR),
        ("lpFile",       wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory",  wintypes.LPCWSTR),
        ("nShow",        ctypes.c_int),
        ("hInstApp",     wintypes.HINSTANCE),
        ("lpIDList",     ctypes.c_void_p),
        ("lpClass",      wintypes.LPCWSTR),
        ("hkeyClass",    wintypes.HKEY),
        ("dwHotKey",     wintypes.DWORD),
        ("hIcon",        wintypes.HANDLE),
        ("hProcess",     wintypes.HANDLE),
    ]

_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SEE_MASK_NOASYNC        = 0x00000100
_SW_HIDE                 = 0
_ERROR_CANCELLED         = 1223
_INFINITE                = 0xFFFFFFFF

# pnputil's own exit codes.
_ERROR_SUCCESS           = 0
_ERROR_SUCCESS_REBOOT    = 3010


def install(timeout_s: int = 180) -> tuple[bool, str]:
    """
    Install the bundled driver. Returns (ok, message).

    Raises a UAC prompt, because adding a driver to the Windows driver store
    needs administrator rights and there is no way around that. A customer who
    declines gets a plain message rather than an error.
    """
    if os.name != "nt":
        return False, "The CP210x driver is for Windows only."
    if not is_available():
        return False, "The driver files are missing from this installation."

    pnputil = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "pnputil.exe")
    if not os.path.isfile(pnputil):
        return False, "Windows could not provide pnputil.exe to install the driver."

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC
    info.hwnd = None
    info.lpVerb = "runas"                       # this is what triggers UAC
    info.lpFile = pnputil
    info.lpParameters = '/add-driver "{}" /install'.format(INF_PATH)
    info.lpDirectory = DRIVER_DIR
    info.nShow = _SW_HIDE

    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error() or ctypes.windll.kernel32.GetLastError()
        if err == _ERROR_CANCELLED:
            return False, ("Installation needs administrator permission.\n"
                           "Nothing was changed.")
        return False, "Could not start the driver installer (error {}).".format(err)

    kernel32 = ctypes.windll.kernel32
    try:
        waited = kernel32.WaitForSingleObject(info.hProcess, timeout_s * 1000)
        if waited != 0:
            return False, ("The driver installer did not finish in time. "
                           "Check Device Manager, or install the driver from "
                           "Silicon Labs directly.")
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        rc = code.value
    finally:
        kernel32.CloseHandle(info.hProcess)

    if rc == _ERROR_SUCCESS:
        return True, ("Driver installed.\n\n"
                      "Unplug the CubeSat and plug it back in, then press "
                      "Refresh. It should appear as a COM port.")
    if rc == _ERROR_SUCCESS_REBOOT:
        return True, ("Driver installed. Windows asked for a restart to "
                      "finish.\n\nRestart, then reconnect the CubeSat.")
    return False, ("The driver could not be installed (pnputil returned {}).\n\n"
                   "This usually means Windows rejected it on this edition. "
                   "Installing from Silicon Labs directly should work."
                   .format(rc))
