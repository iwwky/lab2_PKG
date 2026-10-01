import time
from pathlib import Path
from . import bmp, gif, jpeg, pcx, png, tiff
from .reader import BinaryReader, ParseError, ParseLimit, ParseUnsupported
from ..models import ImageMetadata

EXTENSIONS = {".jpg": "JPEG", ".jpeg": "JPEG", ".jpe": "JPEG", ".jfif": "JPEG", ".gif": "GIF",
              ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP", ".png": "PNG", ".pcx": "PCX"}
PARSERS = {"JPEG": jpeg.parse, "PNG": png.parse, "BMP": bmp.parse, "GIF": gif.parse, "PCX": pcx.parse, "TIFF": tiff.parse}


def identify(signature: bytes) -> str | None:
    if signature.startswith(png.SIGNATURE):
        return "PNG"
    if signature.startswith(b"\xff\xd8"):
        return "JPEG"
    if signature.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if signature.startswith(b"BM"):
        return "BMP"
    if signature.startswith((b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b")):
        return "TIFF"
    if len(signature) >= 4 and signature[0] == 10 and signature[1] in (0, 2, 3, 4, 5):
        return "PCX"
    return None


def inspect_file(path: str | Path) -> ImageMetadata:
    started = time.perf_counter()
    result = ImageMetadata(path=str(Path(path).absolute()))
    reader = None
    expected = EXTENSIONS.get(Path(path).suffix.lower())
    try:
        with open(path, "rb") as stream:
            reader = BinaryReader(stream)
            result.file_size = reader.size
            signature = reader.read(min(16, reader.size))
            detected = identify(signature)
            if detected is None:
                if expected:
                    result.format = expected
                    raise ParseError("Сигнатура не соответствует графическому формату: файл обрезан или расширение подменено")
                result.status = "Не поддерживается"
                result.message = "Неизвестная сигнатура; поддерживаются JPEG, GIF, TIFF, BMP, PNG, PCX"
                return result
            result.format = detected
            if expected != detected:
                result.warnings.append(f"Расширение {Path(path).suffix or '(нет)'} не соответствует содержимому {detected}")
            reader.seek(0)
            PARSERS[detected](reader, result)
            if result.warnings:
                result.status = "Предупреждение"
            result.message = "; ".join(result.warnings)
    except ParseUnsupported as error:
        result.status, result.message = "Не поддерживается", str(error)
    except ParseLimit as error:
        result.status, result.message = "Лимит проверки", str(error)
    except (ParseError, OverflowError, ValueError) as error:
        result.status, result.message = "Файл повреждён", str(error)
    except OSError as error:
        result.status, result.message = "Ошибка доступа", str(error)
    finally:
        result.bytes_read = reader.bytes_read if reader else 0
        result.elapsed_ms = (time.perf_counter() - started) * 1000
    return result
