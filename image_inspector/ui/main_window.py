from pathlib import Path
from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSplitter, QTableView, QVBoxLayout, QWidget)
from ..engine.scanner import ScanJob, ScanSummary
from .preview import PreviewJob
from .table_model import MetadataTableModel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Лаб2 Куделко")
        self.resize(1200, 600)
        self.scan_pool = QThreadPool(self)
        self.scan_pool.setMaxThreadCount(1)
        self.preview_pool = QThreadPool(self)
        self.preview_pool.setMaxThreadCount(1)
        self.job = None
        self.preview_job = None
        self.pending_preview = None
        self.preview_token = 0
        self.preview_pixmap = None
        self.current = None
        self.summary = ScanSummary()
        self.build_ui()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        self.folder_button = QPushButton("Выбрать папку")
        self.files_button = QPushButton("Выбрать файлы")
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setEnabled(False)
        self.folder_button.clicked.connect(self.choose_folder)
        self.files_button.clicked.connect(self.choose_files)
        self.stop_button.clicked.connect(self.cancel_scan)
        for button in (self.folder_button, self.files_button, self.stop_button):
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.source_label = QLabel("Выберите папку или файлы")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.source_label)

        self.model = MetadataTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate((180, 65, 110, 125, 100, 175, 135)):
            self.table.setColumnWidth(col, width)
        self.table.selectionModel().currentRowChanged.connect(self.select_row)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.table)
        panel = QWidget()
        panel.setMinimumWidth(190)
        right = QVBoxLayout(panel)
        right.addWidget(QLabel("Предпросмотр"))
        self.preview_label = QLabel("Выберите строку")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)
        right.addWidget(self.preview_label, 1)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.message_label)
        splitter.addWidget(panel)
        splitter.setSizes([990, 210])
        layout.addWidget(splitter, 1)
        self.progress_text = QLabel("Готово к работе")
        layout.addWidget(self.progress_text)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

    def picker(self, kind):

        attribute = "_picker_" + kind
        dialog = getattr(self, attribute, None)
        if dialog is None:
            title = "Папка с изображениями" if kind == "folder" else "Выберите изображения"
            dialog = QFileDialog(self, title, str(Path.home()))
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
            if kind == "folder":
                dialog.setFileMode(QFileDialog.FileMode.Directory)
                dialog.setOption(QFileDialog.Option.ShowDirsOnly)
            else:
                dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
                dialog.setNameFilters(["Изображения (*.jpg *.jpeg *.jpe *.jfif *.gif *.tif *.tiff *.bmp *.png *.pcx)", "Все файлы (*)"])
            for label, text in [(QFileDialog.DialogLabel.LookIn, "Папка:"),
                                (QFileDialog.DialogLabel.FileName, "Путь:"),
                                (QFileDialog.DialogLabel.FileType, "Тип файлов:"),
                                (QFileDialog.DialogLabel.Accept, "Выбрать"),
                                (QFileDialog.DialogLabel.Reject, "Отмена")]:
                dialog.setLabelText(label, text)
            setattr(self, attribute, dialog)
        return dialog.selectedFiles() if dialog.exec() else []

    def choose_folder(self):
        paths = self.picker("folder")
        if paths:
            self.start_scan(folder=paths[0])

    def choose_files(self):
        paths = self.picker("files")
        if paths:
            self.start_scan(files=paths)

    def set_busy(self, busy):
        self.folder_button.setEnabled(not busy)
        self.files_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)

        self.table.setSortingEnabled(not busy)

    def clear_results(self):
        self.model.clear()
        self.current = None
        self.preview_token += 1
        self.pending_preview = None
        self.preview_pixmap = None
        self.preview_label.clear()
        self.preview_label.setText("Выберите строку")
        self.message_label.clear()
        self.summary = ScanSummary()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_text.setText("Готово к работе")
        self.progress_text.setToolTip("")

    def start_scan(self, *, folder=None, files=None):
        if self.job is not None:
            return
        self.clear_results()
        self.source_label.setText(folder or f"Выбрано файлов: {len(files or [])}")

        self.job = ScanJob(folder=folder, files=files, recursive=True, workers=4)
        self.job.signals.discovery.connect(self.discovery_progress)
        self.job.signals.started.connect(self.processing_started)
        self.job.signals.batch.connect(self.add_batch)
        self.job.signals.progress.connect(self.scan_progress)
        self.job.signals.completed.connect(self.scan_completed)
        self.job.signals.failed.connect(self.scan_failed)
        self.set_busy(True)
        self.progress_bar.setRange(0, 0)
        self.progress_text.setText("Поиск файлов…")
        self.scan_pool.start(self.job)

    @Slot(int, int)
    def discovery_progress(self, found, skipped):
        self.progress_text.setText(f"Поиск файлов: {found:,}")

    @Slot(int)
    def processing_started(self, total):
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(0)
        self.progress_text.setText(f"Обработано: 0 / {total:,}")

    @Slot(list)
    def add_batch(self, batch):
        self.model.add(batch)

    @Slot(int, int)
    def scan_progress(self, processed, total):
        self.progress_bar.setValue(processed)
        self.progress_text.setText(f"Обработано: {processed:,} / {total:,}")

    def cancel_scan(self):
        if self.job:
            self.job.cancel()
            self.stop_button.setEnabled(False)
            self.progress_text.setText("Остановка…")

    @Slot(object)
    def scan_completed(self, summary):
        self.summary = summary
        self.job = None
        self.set_busy(False)
        state = "Остановлено" if summary.cancelled else "Готово"
        message = f"{state}: {summary.processed:,} / {summary.total:,}. Ошибок: {summary.errors}. Время: {summary.elapsed:.2f} с."
        if summary.warnings:
            message += f" Предупреждений: {summary.warnings}."
        if summary.limited:
            message += " Лимит: 100 000 файлов."
        if summary.notices:
            message += f" Недоступных объектов: {len(summary.notices)} (см. подсказку)."
        self.progress_text.setText(message)
        self.progress_text.setToolTip("\n".join(summary.notices))
        if not summary.cancelled:
            self.progress_bar.setValue(max(1, summary.total))
        if self.model.rowCount() and not self.table.currentIndex().isValid():
            self.table.selectRow(0)

    @Slot(str)
    def scan_failed(self, message):
        self.job = None
        self.set_busy(False)
        self.progress_bar.setRange(0, 100)
        self.progress_text.setText("Ошибка сканирования")
        QMessageBox.critical(self, "Ошибка сканирования", message)

    def select_row(self, current, previous):
        self.preview_token += 1
        token = self.preview_token
        self.pending_preview = None
        self.preview_pixmap = None
        self.preview_label.clear()
        self.message_label.clear()
        if not current.isValid():
            self.current = None
            self.preview_label.setText("Выберите строку")
            return
        result = self.model.rows[current.row()]
        self.current = result
        self.message_label.setText(result.message)
        self.preview_label.setToolTip(result.path)
        if result.is_error or not result.width or not result.height:
            self.preview_label.setText("Предпросмотр недоступен")
            return
        if result.width * result.height > 40_000_000:
            self.preview_label.setText("Изображение больше 40 Мп.\nПредпросмотр отключён.")
            return
        self.preview_label.setText("Загрузка…")
        self.pending_preview = (token, result.path)
        self.start_pending_preview()

    def start_pending_preview(self):
        if self.preview_job is not None or self.pending_preview is None:
            return
        token, path = self.pending_preview
        self.pending_preview = None
        self.preview_job = PreviewJob(token, path)
        self.preview_job.signals.ready.connect(self.preview_ready)
        self.preview_pool.start(self.preview_job)

    @Slot(int, object, str)
    def preview_ready(self, token, image, error):
        self.preview_job = None
        if token == self.preview_token:
            if image is None:
                self.preview_label.setText(error)
            else:
                self.preview_pixmap = QPixmap.fromImage(image)
                self.update_preview_size()
        self.start_pending_preview()

    def update_preview_size(self):
        if self.preview_pixmap:
            size = self.preview_label.size()
            self.preview_label.setPixmap(self.preview_pixmap.scaled(max(1, size.width() - 10), max(1, size.height() - 10),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "preview_pixmap"):
            self.update_preview_size()

    def closeEvent(self, event):
        if self.job:
            self.job.cancel()
        self.pending_preview = None
        event.accept()
