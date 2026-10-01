import io
import struct
from typing import BinaryIO


class ParseError(Exception):
    pass


class ParseUnsupported(Exception):
    pass


class ParseLimit(Exception):
    pass


class BinaryReader:
    MAX_READ = 1024 * 1024
    MAX_TOTAL = 16 * 1024 * 1024

    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.stream.seek(0, io.SEEK_END)
        self.size = self.stream.tell()
        self.stream.seek(0)
        self.bytes_read = 0

    def tell(self) -> int:
        return self.stream.tell()

    def check_range(self, offset: int, count: int) -> None:
        if offset < 0 or count < 0 or offset > self.size or count > self.size - offset:
            raise ParseError(f"Участок {offset}…{offset + count} выходит за размер файла ({self.size} байт)")

    def seek(self, offset: int) -> None:
        self.check_range(offset, 0)
        self.stream.seek(offset)

    def skip(self, count: int) -> None:
        self.seek(self.tell() + count)

    def read(self, count: int) -> bytes:
        self.check_range(self.tell(), count)
        if count > self.MAX_READ or self.bytes_read + count > self.MAX_TOTAL:
            raise ParseLimit("Превышен безопасный объём чтения метаданных")
        data = self.stream.read(count)
        self.bytes_read += len(data)
        if len(data) != count:
            raise ParseError("Файл обрезан или изменился во время чтения")
        return data

    def at(self, offset: int, count: int) -> bytes:
        self.seek(offset)
        return self.read(count)

    def unpack(self, fmt: str) -> tuple:
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))

    def u8(self) -> int:
        return self.read(1)[0]


def dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ParseError("Ширина и высота должны быть положительными")


def dpi_from_meter(value: int) -> float | None:
    return value * 0.0254 if value > 0 else None
