"""Own a subprocess tree and stop it with bounded graceful/forced cleanup."""
import ctypes
import os
import signal
import subprocess
from ctypes import wintypes


class WindowsJob:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        api = self.api
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(api, name)
            fn.argtypes, fn.restype = args, result

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IO),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        self.handle = api.CreateJobObjectW(None, None)
        self.check(self.handle)
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            self.check(api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def attach_and_resume(self, proc):
        self.check(self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(proc._handle))))

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                        ("tid", wintypes.DWORD), ("pid", wintypes.DWORD),
                        ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                        ("flags", wintypes.DWORD)]

        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.pid == proc.pid:
                    thread = self.check(self.api.OpenThread(2, False, entry.tid))
                    try:
                        if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.api.CloseHandle(thread)
                    return
                found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise RuntimeError("Suspended child has no primary thread")
        finally:
            self.api.CloseHandle(snapshot)

    def terminate(self):
        self.check(self.api.TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def spawn_owned(command, **kwargs):
    """Contain the child before it can create descendants."""
    job = WindowsJob() if os.name == "nt" else None
    proc = None
    try:
        if job:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x4  # CREATE_SUSPENDED
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **kwargs)
        proc._relay_job = job
        proc._relay_pgid = proc.pid
        if job:
            job.attach_and_resume(proc)
        return proc
    except BaseException:
        if proc is not None:
            proc.kill()
            proc.wait(timeout=5)
        if job:
            job.close()
        raise


def stop_owned(proc, grace=15, force_timeout=5):
    """Stop the parent, remove remaining descendants, and reap the parent."""
    if getattr(proc, "_relay_stopped", False) is True:
        return proc._relay_shutdown
    job = getattr(proc, "_relay_job", None)
    pgid = getattr(proc, "_relay_pgid", None)
    forced = False
    try:
        if proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                elif pgid is not None:
                    os.killpg(pgid, signal.SIGTERM)
                else:
                    proc.terminate()
            except (ProcessLookupError, PermissionError, OSError):
                forced = True
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                forced = True
        # Descendants may still be alive even when the parent exited gracefully.
        if job:
            job.terminate()
        elif pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif proc.poll() is None:
            proc.kill()
        proc.wait(timeout=force_timeout)
        proc._relay_shutdown = {"forced_parent_shutdown": forced, "exit_code": proc.returncode}
        proc._relay_stopped = True
        return proc._relay_shutdown
    finally:
        if job:
            job.close()
