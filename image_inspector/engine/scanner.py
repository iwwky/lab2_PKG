import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, Signal
from ..parsers import EXTENSIONS, inspect_file

MAX_FILES = 100_000


@dataclass(slots=True)
class ScanSummary:
    total: int = 0
    processed: int = 0
    errors: int = 0
    warnings: int = 0
    skipped: int = 0
    bytes_read: int = 0
    file_bytes: int = 0
    elapsed: float = 0.0
    cancelled: bool = False
    limited: bool = False
    notices: tuple[str, ...] = ()


class ScanSignals(QObject):
    discovery = Signal(int, int)
    started = Signal(int)
    batch = Signal(list)
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)


class ScanJob(QRunnable):
    def __init__(self, *, folder: str | None = None, files: list[str] | None = None,
                 recursive: bool = True, all_files: bool = False, workers: int = 4):
        super().__init__()
        self.signals = ScanSignals()
        self.folder = folder
        self.files = files or []
        self.recursive, self.all_files = recursive, all_files
        self.workers = max(1, min(16, workers))
        self.cancel_event = threading.Event()
        self.summary = ScanSummary()
        self.notices = []

    def cancel(self) -> None:
        self.cancel_event.set()

    def discover(self) -> list[str]:
        selected = []
        if self.folder is None:

            for path in dict.fromkeys(self.files):
                if self.cancel_event.is_set():
                    break
                if len(selected) == MAX_FILES:
                    self.summary.limited = True
                    break
                selected.append(path)
            return selected
        pending_folders = [self.folder]
        last_update = 0.0
        while pending_folders and not self.cancel_event.is_set():
            current = pending_folders.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if self.cancel_event.is_set():
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if self.recursive and not entry.name.startswith('.'):
                                    pending_folders.append(entry.path)
                                continue
                            if not entry.is_file(follow_symlinks=False) or entry.name.startswith('.'):
                                continue
                            if not self.all_files and Path(entry.name).suffix.lower() not in EXTENSIONS:
                                self.summary.skipped += 1
                                continue
                            if len(selected) == MAX_FILES:
                                self.summary.limited = True
                                return selected
                            selected.append(entry.path)
                        except OSError as error:
                            if len(self.notices) < 20:
                                self.notices.append(f"{entry.path}: {error}")
                        now = time.perf_counter()
                        if now - last_update >= 0.1:
                            self.signals.discovery.emit(len(selected), self.summary.skipped)
                            last_update = now
            except OSError as error:
                if len(self.notices) < 20:
                    self.notices.append(f"{current}: {error}")
        return selected

    def run(self) -> None:
        start = time.perf_counter()
        try:
            paths = self.discover()
            total = len(paths)
            self.summary.total = total
            self.signals.started.emit(total)
            batch = []
            last_delivery = time.perf_counter()
            iterator = iter(paths)
            pending = set()
            exhausted = False
            with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="metadata") as pool:
                while pending or not exhausted:
                    if self.cancel_event.is_set():
                        for future in pending:
                            future.cancel()
                        break

                    while not exhausted and len(pending) < self.workers * 2:
                        try:
                            path = next(iterator)
                        except StopIteration:
                            exhausted = True
                            break
                        pending.add(pool.submit(inspect_file, path))
                    if not pending:
                        break
                    done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        result = future.result()
                        batch.append(result)
                        self.summary.processed += 1
                        self.summary.errors += int(result.is_error)
                        self.summary.warnings += int(result.status == "Предупреждение")
                        self.summary.bytes_read += result.bytes_read
                        self.summary.file_bytes += result.file_size
                    now = time.perf_counter()
                    if batch and (len(batch) >= 100 or now - last_delivery >= 0.08):
                        self.signals.batch.emit(batch)
                        batch = []
                        self.signals.progress.emit(self.summary.processed, total)
                        last_delivery = now
                if batch:
                    self.signals.batch.emit(batch)
            self.summary.cancelled = self.cancel_event.is_set()
            self.summary.elapsed = time.perf_counter() - start
            self.summary.notices = tuple(self.notices)
            self.signals.progress.emit(self.summary.processed, total)
            self.signals.completed.emit(self.summary)
        except Exception as error:

            self.signals.failed.emit(f"{type(error).__name__}: {error}")
