"""Typed Win32 process memory adapter; standard library only."""
import ctypes as C
from ctypes import wintypes as W
import struct

k32 = C.WinDLL('kernel32', use_last_error=True)
SIZE = C.c_size_t
INVALID = C.c_void_p(-1).value


class ProcessEntry(C.Structure):
    _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('th32ProcessID', W.DWORD),
                ('th32DefaultHeapID', SIZE), ('th32ModuleID', W.DWORD), ('cntThreads', W.DWORD),
                ('th32ParentProcessID', W.DWORD), ('pcPriClassBase', W.LONG),
                ('dwFlags', W.DWORD), ('szExeFile', W.WCHAR * 260)]


class ModuleEntry(C.Structure):
    _fields_ = [('dwSize', W.DWORD), ('th32ModuleID', W.DWORD), ('th32ProcessID', W.DWORD),
                ('GlblcntUsage', W.DWORD), ('ProccntUsage', W.DWORD), ('modBaseAddr', C.c_void_p),
                ('modBaseSize', W.DWORD), ('hModule', W.HMODULE), ('szModule', W.WCHAR * 256),
                ('szExePath', W.WCHAR * 260)]


def api(name, restype, *args):
    f = getattr(k32, name)
    f.restype, f.argtypes = restype, args
    return f


snapshot = api('CreateToolhelp32Snapshot', W.HANDLE, W.DWORD, W.DWORD)
close_handle = api('CloseHandle', W.BOOL, W.HANDLE)
process_first = api('Process32FirstW', W.BOOL, W.HANDLE, C.POINTER(ProcessEntry))
process_next = api('Process32NextW', W.BOOL, W.HANDLE, C.POINTER(ProcessEntry))
module_first = api('Module32FirstW', W.BOOL, W.HANDLE, C.POINTER(ModuleEntry))
module_next = api('Module32NextW', W.BOOL, W.HANDLE, C.POINTER(ModuleEntry))
open_process = api('OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
query_path = api('QueryFullProcessImageNameW', W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
read_memory = api('ReadProcessMemory', W.BOOL, W.HANDLE, C.c_void_p, C.c_void_p, SIZE, C.POINTER(SIZE))
write_memory = api('WriteProcessMemory', W.BOOL, W.HANDLE, C.c_void_p, C.c_void_p, SIZE, C.POINTER(SIZE))
wait_process = api('WaitForSingleObject', W.DWORD, W.HANDLE, W.DWORD)


def check(ok):
    if not ok:
        raise C.WinError(C.get_last_error())
    return ok


def enumerate_entries(flags, pid, kind, first, next_entry):
    handle = snapshot(flags, pid)
    if handle == INVALID:
        raise C.WinError(C.get_last_error())
    try:
        entry = kind()
        entry.dwSize = C.sizeof(entry)
        ok = first(handle, C.byref(entry))
        while ok:
            yield kind.from_buffer_copy(entry)
            ok = next_entry(handle, C.byref(entry))
        error = C.get_last_error()
        if error != 18:
            raise C.WinError(error)
    finally:
        close_handle(handle)


def processes():
    return enumerate_entries(2, 0, ProcessEntry, process_first, process_next)


class ProcessMemory:
    def __init__(self, pid, writable=False):
        self.handle = None
        if C.sizeof(C.c_void_p) != 8:
            raise RuntimeError('请使用 64 位 Python。')
        self.pid = pid
        rights = 0x1000 | 0x10 | 0x100000
        if writable:
            rights |= 0x20 | 0x8
        self.handle = check(open_process(rights, False, pid))

    def close(self):
        if self.handle:
            close_handle(self.handle)
            self.handle = None

    def __del__(self):
        self.close()

    def alive(self):
        if not self.handle or wait_process(self.handle, 0) != 258:
            raise RuntimeError('游戏进程已退出，请重新连接。')

    def path(self):
        self.alive()
        buf = C.create_unicode_buffer(32768)
        size = W.DWORD(len(buf))
        check(query_path(self.handle, 0, buf, C.byref(size)))
        return buf.value

    def module(self, name):
        self.alive()
        for e in enumerate_entries(0x8 | 0x10, self.pid, ModuleEntry, module_first, module_next):
            if e.szModule.casefold() == name.casefold():
                return dict(base=e.modBaseAddr, size=e.modBaseSize, path=e.szExePath, pid=self.pid)
        raise RuntimeError('找不到模块：' + name)

    def read(self, address, size):
        self.alive()
        if not 0 < address < 0x800000000000 or not 0 < size <= 16 * 1024 * 1024:
            raise ValueError('无效的读取地址或长度。')
        buf, done = C.create_string_buffer(size), SIZE()
        check(read_memory(self.handle, address, buf, size, C.byref(done)))
        if done.value != size:
            raise RuntimeError('内存读取不完整。')
        return buf.raw

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def i32(self, address):
        return struct.unpack('<i', self.read(address, 4))[0]

    def write_i32(self, address, value):
        self.write_four(address, struct.pack('<i', value))

    def write_f32(self, address, value):
        self.write_four(address, struct.pack('<f', value))

    def write_four(self, address, data):
        self.alive()
        if not 0 < address < 0x800000000000 or len(data) != 4:
            raise ValueError('无效的写入地址或长度。')
        done = SIZE()
        buf = C.create_string_buffer(data, 4)
        check(write_memory(self.handle, address, buf, 4, C.byref(done)))
        if done.value != 4:
            raise RuntimeError('内存写入不完整，请刷新检查。')
