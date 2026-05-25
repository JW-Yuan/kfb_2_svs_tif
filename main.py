"""
KFB batch conversion GUI using KFBIO's ``KFbioConverter.exe`` with PyQt5.
"""

from glob import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# region agent log
_AGENT_LOG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "debug-1e2795.log")
)


def _agent_debug_log(
    hypothesis_id: str, location: str, message: str, data: Dict[str, Any]
) -> None:
    """Append one NDJSON debug line for DEBUG MODE (session 1e2795)."""
    try:
        payload = {
            "sessionId": "1e2795",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(_AGENT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# endregion

from PyQt5.QtCore import QObject, pyqtSignal, QThread, Qt
from PyQt5.QtGui import QIcon, QTextCursor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
)


# Locate the bundled KFBIO converter executable alongside frozen or unpacked assets.


def resolve_kfbio_converter() -> Tuple[Optional[str], Optional[str]]:
    """
    Locate ``KFbioConverter.exe`` and the working directory that should load DLLs.

    Checks, in order: bundled resources under ``sys._MEIPASS`` (PyInstaller),
    executable directory when shipping ``kfbio`` next to the exe, then the script
    directory when running from source.

    Parameters:
        None — uses ``sys``, ``sys._MEIPASS`` (when set), ``sys.executable``, and ``__file__``.

    Returns:
        exe_path: str — absolute path to ``KFbioConverter.exe`` if found, else ``None``.
        cwd: str — absolute path to ``.../kfbio/x86`` (DLL load directory), else ``None``.
    """
    search_roots: List[str] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            search_roots.append(meipass)
        search_roots.append(os.path.dirname(sys.executable))
    else:
        search_roots.append(os.path.dirname(os.path.abspath(__file__)))

    for root in search_roots:
        exe = os.path.normpath(os.path.join(root, "kfbio", "x86", "KFbioConverter.exe"))
        if os.path.isfile(exe):
            return exe, os.path.dirname(exe)

    return None, None


# Locate bundled ``images/logo.png`` for window and taskbar icons.


def resolve_app_icon_path() -> Optional[str]:
    """
    Locate ``images/logo.png`` for application and window icons.

    Checks, in order when frozen: ``sys._MEIPASS``, directory next to the
    executable; when running from source: directory containing ``main.py``.

    Returns:
        path: str — absolute path to the PNG if found, else ``None``.
    """
    rel = os.path.join("images", "logo.png")
    search_roots: List[str] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            search_roots.append(meipass)
        search_roots.append(os.path.dirname(sys.executable))
    else:
        search_roots.append(os.path.dirname(os.path.abspath(__file__)))

    for root in search_roots:
        candidate = os.path.normpath(os.path.join(root, rel))
        if os.path.isfile(candidate):
            return candidate
    return None


def apply_app_icons(app: QApplication, window: Optional[QDialog] = None) -> None:
    """
    Set ``QApplication`` and optional top-level window icons when PNG exists.

    Parameters:
        app: QApplication — running Qt application instance.
        window: QDialog | None — main dialog to receive the same icon.

    Returns:
        None — no-op when ``resolve_app_icon_path`` returns ``None``.
    """
    icon_path = resolve_app_icon_path()
    if not icon_path:
        return
    icon = QIcon(icon_path)
    app.setWindowIcon(icon)
    if window is not None:
        window.setWindowIcon(icon)


# Produce a filesystem root string that matches where resources are unpacked.


def get_application_base_dir_for_logging() -> str:
    """
    Report a readable base directory for diagnostics (frozen vs source).

    Parameters:
        None — uses frozen state and paths from ``resolve_kfbio_converter``.

    Returns:
        path: str — directory used when searching for bundled resources or the script folder.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and os.path.isfile(
            os.path.join(meipass, "kfbio", "x86", "KFbioConverter.exe")
        ):
            return meipass
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# Backwards-compatible alias for paths that mirrored old ``BASE_DIR``.
BASE_DIR = get_application_base_dir_for_logging()


# Enumerate ``*.kfb`` files residing directly underneath a supplied folder.


def get_kfb_file(root_path: str) -> List[str]:
    """
    Enumerate ``*.kfb`` files immediately under a folder, sorted alphabetically.

    Parameters:
        root_path: str — directory to scan.

    Returns:
        files: list[str] — full paths for each matched ``*.kfb`` entry.
    """
    return sorted(glob(os.path.join(root_path, "*.kfb")))


def _win_get_short_path_name(path: str) -> Optional[str]:
    """
    Return the Win32 short (8.3) form of ``path`` when the API succeeds.

    Parameters:
        path: str — existing file or directory path.

    Returns:
        short_path: str — short path without spaces when available, else ``None``.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None
    kernel32 = ctypes.windll.kernel32
    GetShortPathNameW = kernel32.GetShortPathNameW
    GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    GetShortPathNameW.restype = wintypes.DWORD
    needed = int(GetShortPathNameW(path, None, 0))
    if needed == 0:
        return None
    buf = ctypes.create_unicode_buffer(needed)
    if int(GetShortPathNameW(path, buf, needed)) == 0:
        return None
    return buf.value


def path_for_kfbio_converter_cli(path: str) -> str:
    """
    Prefer Win32 short (8.3) paths so ``KFbioConverter.exe`` sees fewer whitespace issues.

    Parameters:
        path: str — absolute input ``.kfb`` or destination slide path.

    Returns:
        cli_path: str — short path when it removes spaces, otherwise ``normpath(path)``.
    """
    path = os.path.normpath(path)
    if sys.platform != "win32":
        return path
    if os.path.isfile(path):
        sp = _win_get_short_path_name(path)
        if sp:
            sp_norm = os.path.normpath(sp)
            if " " not in sp_norm:
                return sp_norm
    else:
        parent, name = os.path.split(path)
        if parent and os.path.isdir(parent):
            sp_parent = _win_get_short_path_name(parent)
            if sp_parent:
                candidate = os.path.normpath(os.path.join(os.path.normpath(sp_parent), name))
                if " " not in candidate:
                    return candidate
    return path


def prepare_kfbio_cli_paths(
    index: int,
    kfb_abs: str,
    out_abs: str,
    output_format: str,
) -> Tuple[str, str, Optional[str]]:
    """
    Build argv paths for ``KFbioConverter.exe``.

    When either path contains an ASCII space on Windows, use a temp directory with
    space-free names (hardlink the ``.kfb`` when possible, else copy). The third return
    value is that temp directory so callers can delete it after each slide.

    Parameters:
        index: int — 1-based slide index (keeps temp names unique).
        kfb_abs: str — absolute source ``.kfb``.
        out_abs: str — intended final output slide path.
        output_format: str — ``svs`` or ``tif`` stem for the temp output filename.

    Returns:
        cli_kfb: str — path passed as argv #2.
        cli_out: str — path passed as argv #3.
        staging_dir: str | None — temp directory to remove after the slide, or ``None``.
    """
    kfb_abs = os.path.normpath(kfb_abs)
    out_abs = os.path.normpath(out_abs)
    if sys.platform != "win32":
        return kfb_abs, out_abs, None

    if (" " not in kfb_abs) and (" " not in out_abs):
        return (
            path_for_kfbio_converter_cli(kfb_abs),
            path_for_kfbio_converter_cli(out_abs),
            None,
        )

    tdir = tempfile.mkdtemp(prefix="kfbio_sp_")
    cli_kfb = os.path.join(tdir, f"in{index}.kfb")
    cli_out = os.path.join(tdir, f"out{index}.{output_format}")
    try:
        try:
            os.link(kfb_abs, cli_kfb)
        except OSError:
            shutil.copy2(kfb_abs, cli_kfb)
    except Exception:
        shutil.rmtree(tdir, ignore_errors=True)
        raise
    return cli_kfb, cli_out, tdir


# Discrete batch progress: only ``k/n`` steps when ``k`` files have finished (no per-file heuristic).


def batch_discrete_percent(finished_files: int, total: int) -> int:
    """
    Return ``0–100`` from how many slides have completed (success or failure), ``total`` slides.

    While slide ``k`` is running, callers should pass ``finished_files = k - 1`` (often ``0``).
    After slide ``k`` finishes, pass ``finished_files = k`` so the bar jumps by one ``1/total`` step.

    Parameters:
        finished_files: int — count of slides already finished in ``[0, total]``.
        total: int — slides in the batch.

    Returns:
        percent: int — ``(100 * finished_files) // total`` capped at ``100``.
    """
    if total <= 0:
        return 100
    ff = min(total, max(0, int(finished_files)))
    return min(100, (100 * ff) // total)


class ConversionWorker(QThread):
    """
    Runs batch ``KFbioConverter.exe`` subprocess calls without blocking the UI thread.

    Emits ``file_started``, ``batch_progress`` (discrete ``k/n`` steps), ``log_line``, and
    ``batch_finished(success, fail, aborted)``.
    """

    file_started = pyqtSignal(int, int, str)
    """Emits ``(index_1based, total, basename)`` when a slide conversion begins."""

    batch_progress = pyqtSignal(int)
    """Emits overall batch percent ``0–100`` in ``1/total`` jumps when each slide finishes."""

    log_line = pyqtSignal(str)
    """Emits a single UTF-8 log line."""

    batch_finished = pyqtSignal(int, int, bool)
    """Emits ``(success_count, fail_count, aborted)`` when the queue stops."""

    # Store conversion-related parameters queued for threaded execution.


    def __init__(
        self,
        kfb_files: List[str],
        output_dir: str,
        output_format: str,
        converter_exe: str,
        converter_cwd: str,
        level: int = 9,
        parent: Optional[QObject] = None,
    ):
        """
        Attach conversion parameters executed on ``run``.

        Parameters:
            kfb_files: list[str] — input ``.kfb`` paths to convert (order preserved).
            output_dir: str — directory receiving outputs (created if missing).
            output_format: str — output extension stem: ``svs`` or ``tif``.
            converter_exe: str — absolute ``KFbioConverter.exe`` path.
            converter_cwd: str — working directory passed to subprocess (DLL folder).
            level: int — converter pyramid/level argument (matches prior default ``9``).
            parent: QObject | None — optional Qt parent thread owner.

        Returns:
            None — initializes thread state until ``run`` executes.
        """
        super().__init__(parent)
        self._kfb_files = list(kfb_files)
        self._output_dir = output_dir
        self._output_format = output_format.strip().lower()
        self._converter_exe = converter_exe
        self._converter_cwd = converter_cwd
        self._level = level
        self._cancel = threading.Event()
        self._active_proc: Optional[subprocess.Popen] = None

    # Ask the worker thread to stop after terminating the active subprocess.


    def request_cancel(self) -> None:
        """
        Request cooperative cancellation of the in-flight conversion (GUI-safe).

        Parameters:
            None — toggles an internal threading event checked by ``run``.

        Returns:
            None — does not block until the worker exits.
        """
        self._cancel.set()

    def force_shutdown(self) -> None:
        """Cancel the batch and kill any in-flight ``KFbioConverter`` child (GUI-safe)."""
        self._cancel.set()
        proc = self._active_proc
        if proc is not None and proc.poll() is None:
            self._terminate_process(proc)

    # Iterate queued conversions with cancellable ``Popen`` polling off the GUI thread.


    def run(self) -> None:
        """
        Convert each queued file using ``Popen`` polling so the UI can show live progress.

        Parameters:
            None — uses ``self._`` fields set in ``__init__``.

        Returns:
            None — completion is signaled via ``batch_finished`` with ``aborted`` flag.
        """
        os.makedirs(self._output_dir, exist_ok=True)
        ok_count = 0
        fail_count = 0
        total = len(self._kfb_files)
        self._cancel.clear()

        # Child ``KFbioConverter.exe`` is a console-subsystem EXE. Without CREATE_NO_WINDOW, Windows
        # allocates a new console for it — users see a flashing Terminal tab (especially when the
        # parent GUI is ``console=False`` / PyInstaller one-file). Subprocess flags do not affect
        # whether conversion succeeds; session 1e2795 later showed failures with ``creationflags=0``
        # as well (root cause was elsewhere: e.g. stdout discarded, paths). Hide the child console.
        creationflags = 0
        if sys.platform == "win32":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

        try:
            for index, kfb_file in enumerate(self._kfb_files, start=1):
                staging_dir: Optional[str] = None
                label = os.path.basename(kfb_file)
                base_name = os.path.splitext(label)[0]
                out_path = os.path.normpath(
                    os.path.join(self._output_dir, f"{base_name}.{self._output_format}")
                )
                kfb_abs = os.path.normpath(kfb_file)
                active_out_path = out_path
                try:
                    cli_kfb, cli_out, staging_dir = prepare_kfbio_cli_paths(
                        index,
                        kfb_abs,
                        out_path,
                        self._output_format,
                    )
                    active_out_path = cli_out if staging_dir else out_path
                    cmd = [
                        os.path.normpath(self._converter_exe),
                        cli_kfb,
                        cli_out,
                        str(self._level),
                    ]
                    self.file_started.emit(index, total, label)
                    self.batch_progress.emit(batch_discrete_percent(index - 1, total))

                    proc: Optional[subprocess.Popen] = None
                    try:
                        proc = subprocess.Popen(
                            cmd,
                            cwd=self._converter_cwd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL,
                            creationflags=creationflags,
                        )
                    except OSError as exc:
                        fail_count += 1
                        self.log_line.emit(f'FAIL "{label}": {exc}')
                        self.batch_progress.emit(batch_discrete_percent(index, total))
                        continue

                    assert proc is not None
                    self._active_proc = proc
                    # region agent log
                    _agent_debug_log(
                        "H4",
                        "main.py:ConversionWorker.run:after_popen",
                        "popen_ok",
                        {
                            "pid": proc.pid,
                            "cwd": self._converter_cwd,
                            "exe": self._converter_exe,
                            "kfb": kfb_file,
                            "kfb_cli": cli_kfb,
                            "kfb_exists": os.path.isfile(kfb_abs),
                            "out_path": out_path,
                            "out_cli": cli_out,
                            "staging_dir": staging_dir,
                            "out_dir": self._output_dir,
                            "out_dir_writable": os.access(self._output_dir, os.W_OK),
                            "creationflags": creationflags,
                            "cmdline": subprocess.list2cmdline(cmd),
                        },
                    )
                    # endregion
                    poll_interval_s = 0.35
                    try:
                        while proc.poll() is None:
                            if self._cancel.is_set():
                                self._terminate_process(proc)
                                self._safe_remove_partial_output(active_out_path)
                                self.log_line.emit(
                                    f'ABORTED "{label}" — partial output removed if present.'
                                )
                                self.batch_finished.emit(ok_count, fail_count, True)
                                return

                            time.sleep(poll_interval_s)

                        rc_raw = proc.returncode
                        rc = int(rc_raw if rc_raw is not None else -1)
                        rc_signed = rc
                        if rc_signed >= 2**31:
                            rc_signed -= 2**32
                        # region agent log
                        _agent_debug_log(
                            "H5",
                            "main.py:ConversionWorker.run:after_wait",
                            "process_exited",
                            {"rc_raw": rc_raw, "rc_int": rc, "rc_signed": rc_signed, "label": label},
                        )
                        # endregion
                        stderr_tail = ""
                        err_bytes = b""
                        try:
                            if proc.stderr is not None:
                                err_bytes = proc.stderr.read() or b""
                                stderr_tail = err_bytes.decode("utf-8", errors="replace").strip()
                        except Exception:
                            stderr_tail = ""

                        stderr_gbk = ""
                        try:
                            stderr_gbk = (err_bytes or b"").decode("gb18030", errors="replace").strip()
                        except Exception:
                            stderr_gbk = ""

                        out_bytes = b""
                        stdout_tail = ""
                        try:
                            if proc.stdout is not None:
                                out_bytes = proc.stdout.read() or b""
                                stdout_tail = out_bytes.decode("utf-8", errors="replace").strip()
                        except Exception:
                            stdout_tail = ""
                        stdout_gbk = ""
                        try:
                            stdout_gbk = (out_bytes or b"").decode("gb18030", errors="replace").strip()
                        except Exception:
                            stdout_gbk = ""

                        # region agent log
                        _agent_debug_log(
                            "H1",
                            "main.py:ConversionWorker.run:streams",
                            "streams_captured",
                            {
                                "err_len": len(err_bytes or b""),
                                "stderr_utf8_tail": (stderr_tail[:800] if stderr_tail else ""),
                                "stderr_gbk_tail": (stderr_gbk[:800] if stderr_gbk else ""),
                                "out_len": len(out_bytes or b""),
                                "stdout_utf8_tail": (stdout_tail[:800] if stdout_tail else ""),
                                "stdout_gbk_tail": (stdout_gbk[:800] if stdout_gbk else ""),
                            },
                        )
                        _agent_debug_log(
                            "H3",
                            "main.py:ConversionWorker.run:output_state",
                            "output_file_probe",
                            {
                                "out_exists": os.path.isfile(active_out_path),
                                "out_size": (
                                    os.path.getsize(active_out_path)
                                    if os.path.isfile(active_out_path)
                                    else 0
                                ),
                            },
                        )
                        # endregion

                        # If the converter exits immediately with -1 and writes nothing to stderr, retry once
                        # via cmd.exe /c + list2cmdline (matches typical manual invocation).
                        if (
                            rc_signed != 0
                            and len(err_bytes or b"") == 0
                            and len(out_bytes or b"") == 0
                        ):
                            cmdline = subprocess.list2cmdline(cmd)
                            # region agent log
                            _agent_debug_log(
                                "H7",
                                "main.py:ConversionWorker.run:shell_retry",
                                "retry_start",
                                {"cmdline": cmdline},
                            )
                            # endregion
                            proc2: Optional[subprocess.Popen] = None
                            try:
                                proc2 = subprocess.Popen(
                                    cmdline,
                                    cwd=self._converter_cwd,
                                    shell=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL,
                                    creationflags=creationflags,
                                )
                            except OSError as exc:
                                _agent_debug_log(
                                    "H7",
                                    "main.py:ConversionWorker.run:shell_retry",
                                    "popen_fail",
                                    {"exc": repr(exc)},
                                )
                            if proc2 is not None:
                                self._active_proc = proc2
                                try:
                                    while proc2.poll() is None:
                                        if self._cancel.is_set():
                                            self._terminate_process(proc2)
                                            self._safe_remove_partial_output(active_out_path)
                                            self.log_line.emit(
                                                f'ABORTED "{label}" — partial output removed if present.'
                                            )
                                            self.batch_finished.emit(ok_count, fail_count, True)
                                            return
                                        time.sleep(poll_interval_s)
                                    rc_raw = proc2.returncode
                                    rc = int(rc_raw if rc_raw is not None else -1)
                                    rc_signed = rc
                                    if rc_signed >= 2**31:
                                        rc_signed -= 2**32
                                    err_bytes = b""
                                    stderr_tail = ""
                                    try:
                                        if proc2.stderr is not None:
                                            err_bytes = proc2.stderr.read() or b""
                                            stderr_tail = err_bytes.decode("utf-8", errors="replace").strip()
                                    except Exception:
                                        stderr_tail = ""
                                    try:
                                        stderr_gbk = (err_bytes or b"").decode("gb18030", errors="replace").strip()
                                    except Exception:
                                        stderr_gbk = ""
                                    out_bytes = b""
                                    stdout_tail = ""
                                    try:
                                        if proc2.stdout is not None:
                                            out_bytes = proc2.stdout.read() or b""
                                            stdout_tail = out_bytes.decode("utf-8", errors="replace").strip()
                                    except Exception:
                                        stdout_tail = ""
                                    try:
                                        stdout_gbk = (out_bytes or b"").decode("gb18030", errors="replace").strip()
                                    except Exception:
                                        stdout_gbk = ""
                                    _agent_debug_log(
                                        "H7",
                                        "main.py:ConversionWorker.run:shell_retry",
                                        "retry_end",
                                        {
                                            "rc_raw": rc_raw,
                                            "rc_signed": rc_signed,
                                            "err_len": len(err_bytes or b""),
                                            "out_len": len(out_bytes or b""),
                                            "out_exists": os.path.isfile(active_out_path),
                                        },
                                    )
                                finally:
                                    if self._active_proc is proc2:
                                        self._active_proc = None

                        if rc_signed == 0:
                            ok_count += 1
                            if staging_dir:
                                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                                if os.path.isfile(out_path):
                                    try:
                                        os.remove(out_path)
                                    except OSError:
                                        pass
                                shutil.move(cli_out, out_path)
                            self.batch_progress.emit(batch_discrete_percent(index, total))
                            self.log_line.emit(f'OK "{label}" -> "{os.path.basename(out_path)}"')
                        else:
                            fail_count += 1
                            if staging_dir:
                                self._safe_remove_partial_output(cli_out)
                            self.batch_progress.emit(batch_discrete_percent(index, total))
                            branch = stderr_gbk if stderr_gbk else stderr_tail
                            if not branch:
                                branch = stdout_gbk if stdout_gbk else stdout_tail
                            detail = ""
                            if branch:
                                detail = branch.splitlines()[-1][:500]
                            self.log_line.emit(
                                f'FAIL "{label}" (exit {rc_signed}) {detail}'.rstrip()
                            )
                    finally:
                        if self._active_proc is proc:
                            self._active_proc = None
                finally:
                    if staging_dir:
                        shutil.rmtree(staging_dir, ignore_errors=True)
        finally:
            if not self._cancel.is_set():
                self.batch_finished.emit(ok_count, fail_count, False)

    # Force-terminate a child process tree best-effort on Windows and POSIX hosts.


    def _terminate_process(self, proc: subprocess.Popen) -> None:
        """
        Terminate a hung ``KFbioConverter`` child process during user cancellation.

        Parameters:
            proc: subprocess.Popen — active handle returned by ``subprocess.Popen``.

        Returns:
            None — waits briefly then escalates to ``kill`` when needed.
        """
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=5.0)
            except Exception:
                pass

    # Remove a partially written slide only after user-triggered cancellation.


    def _safe_remove_partial_output(self, out_path: str) -> None:
        """
        Delete the destination slide if cancellation may have left a partial artifact.

        Parameters:
            out_path: str — absolute output path targeted by the converter.

        Returns:
            None — ignores missing paths or ``OSError`` from concurrent writers.
        """
        try:
            if os.path.isfile(out_path):
                os.remove(out_path)
        except OSError:
            pass


class MainWindow(QDialog):
    """
    Thin dialog that wires folder/file selection to the background converter thread.
    """

    # Compose widgets, threaded worker hooks, and the primary grid layout.


    def __init__(self, parent: Optional[QDialog] = None) -> None:
        """
        Create UI controls mirroring legacy layout while adding logs and threaded work.

        Parameters:
            parent: QDialog | None — optional owning widget supplied by callers.

        Returns:
            None — constructs dialog children and signal wiring.
        """
        super(MainWindow, self).__init__(parent)
        self._worker: Optional[ConversionWorker] = None
        self._shutting_down = False

        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WA_QuitOnClose, True)

        self.resize(720, 680)
        self.setWindowTitle("KFB转SVS/TIF")
        self.file_dialog = QFileDialog(self)
        self.selected_files: List[str] = []
        self.output_dir = ""
        self.last_dir = os.getcwd()

        self.mode_label = QLabel("输入选择")
        self.radio_folder = QRadioButton("选择文件夹")
        self.radio_files = QRadioButton("选择文件")
        self.radio_folder.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_folder)
        self.mode_group.addButton(self.radio_files)

        self.btn_pick_input = QPushButton("选择输入", self)
        self.btn_pick_input.clicked.connect(self.pick_input)

        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.NoSelection)

        self.output_label = QLabel("输出路径")
        self.output_edit = QLineEdit(self)
        self.output_edit.setReadOnly(True)
        self.btn_pick_output = QPushButton("选择输出文件夹", self)
        self.btn_pick_output.clicked.connect(self.pick_output_dir)

        self.format_label = QLabel("输出格式")
        self.format_combo = QComboBox(self)
        self.format_combo.addItems(["svs", "tif"])

        self.btn_start = QPushButton("开始转换", self)
        self.btn_start.clicked.connect(self.start_convert)
        self.btn_abort = QPushButton("中止", self)
        self.btn_abort.setEnabled(False)
        self.btn_abort.clicked.connect(self.request_abort)

        start_abort_row = QHBoxLayout()
        start_abort_row.addWidget(self.btn_start)
        start_abort_row.addWidget(self.btn_abort)

        self.label_progress = QLabel("整体进度")
        self.label_xn = QLabel("批次：— / —")
        self.label_current_file = QLabel("当前文件：—")
        self.create_progress_bar()

        self.label_log = QLabel("转换日志")
        self.log_edit = QTextEdit(self)
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(140)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(self.radio_folder)
        mode_layout.addWidget(self.radio_files)
        mode_layout.addStretch(1)

        progress_column = QVBoxLayout()
        progress_column.addWidget(self.label_xn)
        progress_column.addWidget(self.label_current_file)
        progress_column.addWidget(self.progress_bar)

        main_layout = QGridLayout()
        main_layout.addWidget(self.mode_label, 0, 0)
        main_layout.addLayout(mode_layout, 0, 1, 1, 2)
        main_layout.addWidget(self.btn_pick_input, 1, 0, 1, 3)
        main_layout.addWidget(self.list_widget, 2, 0, 1, 3)
        main_layout.addWidget(self.output_label, 3, 0)
        main_layout.addWidget(self.output_edit, 3, 1)
        main_layout.addWidget(self.btn_pick_output, 3, 2)
        main_layout.addWidget(self.format_label, 4, 0)
        main_layout.addWidget(self.format_combo, 4, 1)
        main_layout.addLayout(start_abort_row, 4, 2)
        main_layout.addWidget(self.label_progress, 5, 0)
        main_layout.addLayout(progress_column, 5, 1, 1, 2)
        main_layout.addWidget(self.label_log, 6, 0)
        main_layout.addWidget(self.log_edit, 7, 0, 1, 3)
        self.setLayout(main_layout)

    def closeEvent(self, event) -> None:
        """Cancel conversion, kill KFbioConverter, and hard-exit the whole process."""
        self._shutting_down = True
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.force_shutdown()
            if not worker.wait(2500):
                worker.terminate()
                worker.wait(1000)
        self._worker = None
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()
        os._exit(0)

    # Populate ``selected_files`` using either a folder scan or native multi-select.


    def pick_input(self) -> None:
        """
        Load ``selected_files`` from either a shallow folder listing or explicit picks.

        Parameters:
            None — reads radio mode and dialogs using ``last_dir`` for navigation.

        Returns:
            None — updates ``selected_files`` and list widget entries.
        """
        if self.radio_folder.isChecked():
            root_path = self.file_dialog.getExistingDirectory(
                self, "选择文件夹路径", self.last_dir
            )
            if not root_path:
                return
            self.last_dir = root_path
            files = get_kfb_file(root_path)
        else:
            files, _ = self.file_dialog.getOpenFileNames(
                self, "选择 KFB 文件", self.last_dir, "KFB Files (*.kfb)"
            )
            if not files:
                return
            self.last_dir = os.path.dirname(files[0])

        self.selected_files = files
        self.refresh_file_list()

    # Ask the user for an output directory mirrored in the readonly line edit.


    def pick_output_dir(self) -> None:
        """
        Prompt for destination directory mirrored in the read-only line edit.

        Parameters:
            None — dialogs reuse ``last_dir`` as browsing hint.

        Returns:
            None — persists ``output_dir`` and refreshed label text.
        """
        out_dir = self.file_dialog.getExistingDirectory(self, "选择输出文件夹", self.last_dir)
        if not out_dir:
            return
        self.output_dir = out_dir
        self.output_edit.setText(out_dir)

    # Render ``selected_files`` paths inside the immutable list widget.


    def refresh_file_list(self) -> None:
        """
        Replace list widget rows with canonical ``selected_files`` ordering.

        Parameters:
            None — reads ``selected_files`` on the owning dialog.

        Returns:
            None — mutates QListWidget rows only.
        """
        self.list_widget.clear()
        for path in self.selected_files:
            self.list_widget.addItem(path)

    # Append translated worker messages at the end of the QTextEdit log surface.


    def append_log_line(self, line: str) -> None:
        """
        Write one log line emitted from ``ConversionWorker`` into the QTextEdit.

        Parameters:
            line: str — message text that should mirror console-friendly diagnostics.

        Returns:
            None — updates ``log_edit`` cursor position for readability.
        """
        self.log_edit.append(line.rstrip())
        self.log_edit.moveCursor(QTextCursor.End)

    # Instantiate and retain the progress bar child widget for repeated updates.


    def create_progress_bar(self) -> None:
        """
        Construct the shared ``QProgressBar`` used for overall batch progress (``0–100``).

        Parameters:
            None — attaches ``progress_bar`` to ``self`` for layout placement.

        Returns:
            None — creates ``progress_bar`` child widget references.
        """
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")

    # Toggle UI controls that should remain locked while a conversion thread runs.


    def set_conversion_controls_enabled(self, enabled: bool) -> None:
        """
        Toggle user interaction for controls that mutate batch settings mid-run.

        Parameters:
            enabled: bool — ``True`` re-enables edits; ``False`` locks them during conversion.

        Returns:
            None — mutates QWidget ``setEnabled`` state for critical controls.
        """
        self.btn_start.setEnabled(enabled)
        self.btn_abort.setEnabled(not enabled)
        self.btn_pick_input.setEnabled(enabled)
        self.btn_pick_output.setEnabled(enabled)
        self.radio_folder.setEnabled(enabled)
        self.radio_files.setEnabled(enabled)
        self.format_combo.setEnabled(enabled)

    # Update batch index labels; overall percent is emitted separately via ``batch_progress``.


    def on_file_started(self, index_1based: int, total: int, basename: str) -> None:
        """
        Refresh batch counters and current-file label; overall percent is driven by ``batch_progress``.

        Parameters:
            index_1based: int — 1-based slide index currently converting.
            total: int — total queued slides for the batch.
            basename: str — filename portion shown under the counters.

        Returns:
            None — mutates ``QLabel`` widgets only.
        """
        self.label_xn.setText(f"批次：{index_1based} / {total}")
        self.label_current_file.setText(f"当前文件：{basename}")

    def on_batch_progress(self, overall_percent: int) -> None:
        """
        Update the progress bar from worker-reported overall batch percent.

        Parameters:
            overall_percent: int — ``0–100`` aggregate across the whole queue.

        Returns:
            None — updates ``progress_bar`` range and value for user feedback.
        """
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(min(100, max(0, int(overall_percent))))

    # Re-enable the dialog and summarize success vs failure once the worker exits.


    def on_batch_finished(self, success_count: int, fail_count: int, aborted: bool) -> None:
        """
        Present a completion dialog and release controls after the worker stops.

        Parameters:
            success_count: int — files whose subprocess finished with code ``0``.
            fail_count: int — files that raised ``OSError`` or non-zero exit codes.
            aborted: bool — ``True`` when the user cancelled mid-batch.

        Returns:
            None — schedules UI re-enablement and user notifications.
        """
        if self._shutting_down:
            return
        self.set_conversion_controls_enabled(True)
        self._worker = None
        self.label_xn.setText("批次：— / —")
        self.label_current_file.setText("当前文件：—")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        if aborted:
            QMessageBox.information(
                self,
                "已中止",
                f"已中止。已成功 {success_count} 个，失败 {fail_count} 个（未删除已完成输出）。",
            )
            return

        if fail_count == 0:
            QMessageBox.information(
                self,
                "完成",
                f"全部 {success_count} 个文件转换完成。",
            )
        else:
            QMessageBox.warning(
                self,
                "完成（部分失败）",
                f"成功 {success_count} 个，失败 {fail_count} 个。详见日志。",
            )

    # Forward cancellation requests to the active worker without blocking the GUI thread.


    def request_abort(self) -> None:
        """
        Ask the running ``ConversionWorker`` to terminate the active subprocess.

        Parameters:
            None — inspects ``self._worker`` for a live handle.

        Returns:
            None — no-op when idle; otherwise calls ``request_cancel`` on the worker.
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()

    # Kick off validations and start the threaded worker if inputs look consistent.


    def start_convert(self) -> None:
        """
        Validate selections, resolve ``KFbioConverter.exe``, and queue ``ConversionWorker``.

        Parameters:
            None — inspects dialog fields and ``resolve_kfbio_converter`` output.

        Returns:
            None — starts ``QThread`` or shows blocking warnings on invalid state.
        """
        if self._worker is not None and self._worker.isRunning():
            return

        if not self.selected_files:
            QMessageBox.warning(self, "提示", "请先选择 KFB 文件或文件夹")
            return
        if not self.output_dir:
            QMessageBox.warning(self, "提示", "请先选择输出文件夹")
            return

        exe_path, converter_cwd = resolve_kfbio_converter()
        if not exe_path or not converter_cwd:
            QMessageBox.critical(self, "错误", "找不到转换程序（KFbioConverter.exe）")
            return

        fmt = self.format_combo.currentText().strip().lower()
        file_cnt = len(self.selected_files)
        self.append_log_line(
            f"Starting batch: {file_cnt} file(s), format .{fmt}"
        )

        self.set_conversion_controls_enabled(False)

        worker = ConversionWorker(
            self.selected_files,
            self.output_dir,
            fmt,
            exe_path,
            converter_cwd,
            level=9,
            parent=self,
        )
        worker.file_started.connect(self.on_file_started)
        worker.batch_progress.connect(self.on_batch_progress)
        worker.log_line.connect(self.append_log_line)
        worker.finished.connect(worker.deleteLater)
        worker.batch_finished.connect(self.on_batch_finished)
        self._worker = worker
        worker.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    print("当前程序根路径:", get_application_base_dir_for_logging())
    main = MainWindow()
    apply_app_icons(app, main)
    main.show()
    sys.exit(app.exec_())
