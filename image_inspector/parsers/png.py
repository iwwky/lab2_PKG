import struct
import zlib
from .reader import BinaryReader, ParseError, ParseLimit, dimensions, dpi_from_meter
from ..models import ImageMetadata

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
COLORS = {0: "Оттенки серого", 2: "RGB", 3: "Индексированная палитра", 4: "Серый + альфа", 6: "RGBA"}


def parse(reader: BinaryReader, result: ImageMetadata) -> None:
    if reader.at(0, 8) != SIGNATURE:
        raise ParseError("Неверная сигнатура PNG")
    seen_header = seen_data = seen_palette = False
    data_ended = False
    color = depth = 0
    chunks = 0
    seen_phys = False
    while reader.tell() < reader.size:
        chunks += 1
        if chunks > 100_000:
            raise ParseLimit("Слишком много PNG-чанков")
        length, kind = reader.unpack(">I4s")
        if length > 0x7fffffff or not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind):
            raise ParseError("Некорректный PNG-чанк")
        reader.check_range(reader.tell(), length + 4)
        if not seen_header and kind != b"IHDR":
            raise ParseError("Первым чанком должен быть IHDR")
        payload = b""
        if kind in {b"IHDR", b"pHYs", b"PLTE", b"IEND"}:
            expected = {b"IHDR": 13, b"pHYs": 9, b"IEND": 0}.get(kind)
            if expected is not None and length != expected:
                raise ParseError(f"Неверный размер {kind.decode()}")
            if kind == b"PLTE" and (length == 0 or length > 768 or length % 3):
                raise ParseError("Некорректный размер палитры PNG")
            payload = reader.read(length)
            expected_crc, = reader.unpack(">I")
            if zlib.crc32(kind + payload) & 0xffffffff != expected_crc:
                raise ParseError(f"Неверная CRC чанка {kind.decode()}")
        else:
            reader.skip(length + 4)
        if kind == b"IHDR":
            if seen_header:
                raise ParseError("Повторный IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            dimensions(width, height)
            if width > 0x7fffffff or height > 0x7fffffff:
                raise ParseError("Размер PNG превышает предел формата")
            if color not in DEPTHS or depth not in DEPTHS[color] or compression != 0 or filtering != 0 or interlace not in (0, 1):
                raise ParseError("Неверные параметры IHDR")
            result.width, result.height = width, height
            result.bit_depth = depth * CHANNELS[color]
            result.compression = "Deflate (без потерь)"
            result.details.update({"Цветовая модель": COLORS[color], "Бит на канал / индекс": str(depth),
                                   "Фильтрация": "Метод 0: адаптивные фильтры строк",
                                   "Чересстрочность": "Adam7" if interlace else "Нет"})
            seen_header = True
        elif kind == b"pHYs":
            if seen_phys or seen_data:
                raise ParseError("Повторный pHYs или pHYs после IDAT")
            x, y, unit = struct.unpack(">IIB", payload)
            if unit not in (0, 1):
                raise ParseError("Неверная единица измерения pHYs")
            if unit == 1:
                result.dpi_x, result.dpi_y = dpi_from_meter(x), dpi_from_meter(y)
            else:
                result.details["Отношение плотностей pHYs"] = f"{x}:{y} (единица не задана)"
            seen_phys = True
        elif kind == b"PLTE":
            if seen_palette or seen_data or color in (0, 4):
                raise ParseError("Недопустимое расположение PLTE")
            if color == 3 and length // 3 > 2 ** depth:
                raise ParseError("Палитра больше диапазона индексов")
            result.details["Цветов в палитре"] = str(length // 3)
            seen_palette = True
        elif kind == b"IDAT":
            if data_ended:
                raise ParseError("Чанки IDAT должны идти подряд")
            if color == 3 and not seen_palette:
                raise ParseError("У индексированного PNG отсутствует PLTE")
            seen_data = True
        elif kind == b"IEND":
            if not seen_data:
                raise ParseError("Отсутствует IDAT")
            if reader.tell() != reader.size:
                result.warnings.append("После IEND есть дополнительные байты")
            result.details["Проверено чанков"] = str(chunks)
            return
        elif 65 <= kind[0] <= 90:
            raise ParseError(f"Неизвестный обязательный чанк {kind.decode()}")
        if seen_data and kind != b"IDAT":
            data_ended = True
    raise ParseError("Отсутствует завершающий чанк IEND")
