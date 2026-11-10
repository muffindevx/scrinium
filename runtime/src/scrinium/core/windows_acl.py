"""Windows ACL in SDDL, through the system API (advapi32). No dependencies or subprocesses.

Only used on Windows: on other systems every method raises `NotImplementedError`.
"""

import ctypes
import sys
from pathlib import Path
from typing import Final

_SE_FILE_OBJECT: Final = 1
_OWNER_SECURITY_INFORMATION: Final = 0x1
_DACL_SECURITY_INFORMATION: Final = 0x4
_PROTECTED_DACL_SECURITY_INFORMATION: Final = 0x80000000
_SDDL_REVISION_1: Final = 1
_TOKEN_QUERY: Final = 0x8
_TOKEN_USER: Final = 1


class WindowsAcl:
    def current_user_sid(self) -> str:
        if sys.platform != "win32":
            raise NotImplementedError("Windows only")
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]

        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD()
            # The first call only says how much space is needed.
            advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(
                token, _TOKEN_USER, buffer, size, ctypes.byref(size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            # TOKEN_USER starts with the pointer to the SID.
            sid = ctypes.c_void_p.from_buffer(buffer).value
            text = ctypes.c_void_p()
            if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return ctypes.wstring_at(text.value)
            finally:
                kernel32.LocalFree(text)
        finally:
            kernel32.CloseHandle(token)

    def read(self, path: Path) -> str:
        """Owner and DACL in SDDL: `O:<sid>D:<flags>(ace)(ace)...`."""
        if sys.platform != "win32":
            raise NotImplementedError("Windows only")
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi32.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]

        info = _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION
        descriptor = ctypes.c_void_p()
        error = advapi32.GetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT, info, None, None, None, None, ctypes.byref(descriptor)
        )
        if error:
            raise ctypes.WinError(error)
        try:
            text = ctypes.c_void_p()
            if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, _SDDL_REVISION_1, info, ctypes.byref(text), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return ctypes.wstring_at(text.value)
            finally:
                kernel32.LocalFree(text)
        finally:
            kernel32.LocalFree(descriptor)

    def write_dacl(self, path: Path, dacl: str) -> None:
        """Replaces the DACL with `dacl` (SDDL `D:P...`), without inheriting from the parent."""
        if sys.platform != "win32":
            raise NotImplementedError("Windows only")
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]
        advapi32.GetSecurityDescriptorDacl.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        ]
        advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi32.SetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]

        descriptor = ctypes.c_void_p()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            dacl, _SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            present, defaulted = wintypes.BOOL(), wintypes.BOOL()
            acl = ctypes.c_void_p()
            if not advapi32.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            # Also propagates the inheritable entries to what is already inside.
            error = advapi32.SetNamedSecurityInfoW(
                str(path),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                acl,
                None,
            )
            if error:
                raise ctypes.WinError(error)
        finally:
            kernel32.LocalFree(descriptor)
