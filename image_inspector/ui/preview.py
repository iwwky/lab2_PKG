import warnings
from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage


class PreviewSignals(QObject):
    ready = Signal(int, object, str)


class PreviewJob(QRunnable):
    def __init__(self, token: int, path: str):
        super().__init__()
        self.token, self.path = token, path
        self.signals = PreviewSignals()

    def run(self):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(self.path) as image:

                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((900, 700), Image.Resampling.LANCZOS)
                    rgba = image.convert("RGBA")

                    frame = QImage(rgba.tobytes(), rgba.width, rgba.height,
                                   rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
            self.signals.ready.emit(self.token, frame, "")
        except Exception as error:
            self.signals.ready.emit(self.token, None, f"Предпросмотр недоступен: {error}")
