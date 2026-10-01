import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from image_inspector.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Лаб2 Куделко")
    app.setOrganizationName("PKG Lab")
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1]).expanduser()
        if path.is_dir():
            QTimer.singleShot(0, lambda: window.start_scan(folder=str(path)))
        elif path.is_file():
            QTimer.singleShot(0, lambda: window.start_scan(files=[str(path)]))
    exit_code = app.exec()

    window.scan_pool.waitForDone()
    window.preview_pool.waitForDone()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
