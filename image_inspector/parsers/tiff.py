import struct
from dataclasses import dataclass
from .reader import BinaryReader, ParseError, ParseLimit, dimensions
from ..models import ImageMetadata

TYPES = {1: (1, "B"), 2: (1, "B"), 3: (2, "H"), 4: (4, "I"), 5: (8, "II"),
         6: (1, "b"), 7: (1, "B"), 8: (2, "h"), 9: (4, "i"), 10: (8, "ii"),
         11: (4, "f"), 12: (8, "d"), 13: (4, "I"), 16: (8, "Q"), 17: (8, "q"), 18: (8, "Q")}
COMPRESSION = {1: "Без сжатия", 2: "CCITT Modified Huffman", 3: "CCITT Group 3", 4: "CCITT Group 4",
               5: "LZW", 6: "JPEG (старый TIFF)", 7: "JPEG", 8: "Adobe Deflate", 32773: "PackBits",
               32946: "Deflate", 34887: "LERC", 50000: "ZSTD", 50001: "WebP"}
PHOTOMETRIC = {0: "Серый (0 = белый)", 1: "Серый (0 = чёрный)", 2: "RGB", 3: "Палитра",
               4: "Маска", 5: "CMYK / цветоделение", 6: "YCbCr", 8: "CIELAB"}


@dataclass(slots=True)
class Field:
    kind: int
    count: int
    offset: int


class TiffDirectoryReader:
    def __init__(self, reader: BinaryReader):
        self.reader = reader
        order = reader.at(0, 2)
        if order not in (b"II", b"MM"):
            raise ParseError("Неверный порядок байтов TIFF")
        self.endian = "<" if order == b"II" else ">"
        version, = reader.unpack(self.endian + "H")
        self.big = version == 43
        if version == 42:
            self.first_ifd, = reader.unpack(self.endian + "I")
            self.inline, self.entry_size, self.count_size = 4, 12, 2
        elif version == 43:
            offset_size, reserved, self.first_ifd = reader.unpack(self.endian + "HHQ")
            if offset_size != 8 or reserved != 0:
                raise ParseError("Неверный заголовок BigTIFF")
            self.inline, self.entry_size, self.count_size = 8, 20, 8
        else:
            raise ParseError(f"Неверная версия TIFF: {version}")
        self.header_size = 16 if self.big else 8
        if self.first_ifd < self.header_size:
            raise ParseError("Нет корректного первого IFD")

    def directory(self, offset: int) -> tuple[dict[int, Field], int]:
        if offset < self.header_size:
            raise ParseError("IFD перекрывает заголовок TIFF")
        reader = self.reader
        reader.seek(offset)
        count, = reader.unpack(self.endian + ("Q" if self.big else "H"))
        if count > 8192:
            raise ParseLimit("IFD содержит более 8192 тегов")
        reader.check_range(reader.tell(), count * self.entry_size + self.inline)
        entries_start = reader.tell()
        raw = reader.read(count * self.entry_size)
        next_ifd, = reader.unpack(self.endian + ("Q" if self.big else "I"))
        fields = {}
        for index in range(count):
            position = index * self.entry_size
            tag, kind, size = struct.unpack_from(self.endian + ("HHQ" if self.big else "HHI"), raw, position)
            if tag in fields:
                raise ParseError(f"Повторный TIFF-тег {tag}")
            if kind not in TYPES:
                continue
            length = size * TYPES[kind][0]
            value_position = position + (12 if self.big else 8)
            if length <= self.inline:
                location = entries_start + value_position
            else:
                location, = struct.unpack_from(self.endian + ("Q" if self.big else "I"), raw, value_position)
                if location < self.header_size:
                    raise ParseError(f"Неверное смещение TIFF-тега {tag}")
            reader.check_range(location, length)
            fields[tag] = Field(kind, size, location)
        return fields, next_ifd

    def values(self, field: Field, limit: int = 100_000) -> tuple:
        if field.count > limit:
            raise ParseLimit("Слишком длинный массив значений TIFF")
        size, fmt = TYPES[field.kind]
        raw = self.reader.at(field.offset, size * field.count)
        if field.kind in (5, 10):
            output = []
            for numerator, denominator in struct.iter_unpack(self.endian + fmt, raw):
                if denominator == 0:
                    raise ParseError("Нулевой знаменатель RATIONAL в TIFF")
                output.append(numerator / denominator)
            return tuple(output)
        return tuple(value[0] for value in struct.iter_unpack(self.endian + fmt, raw))

    def scalar(self, fields: dict, tag: int, default=None):
        if tag not in fields:
            return default
        field = fields[tag]
        if field.count != 1:
            raise ParseError(f"Тег {tag} должен содержать одно значение")
        if field.kind not in (1, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 17, 18):
            raise ParseError(f"У тега {tag} неверный тип")
        return self.values(field, 1)[0]

    def resolution(self, fields: dict) -> tuple[float | None, float | None, str]:
        x = self.scalar(fields, 282)
        y = self.scalar(fields, 283)
        unit = self.scalar(fields, 296, 2)
        if unit not in (1, 2, 3):
            raise ParseError("Неверная единица ResolutionUnit TIFF")
        for value in (x, y):
            if value is not None and (value <= 0 or value != value or value == float('inf')):
                raise ParseError("Неверное разрешение TIFF")
        if unit == 1:
            return None, None, "Единица не задана: абсолютный DPI неизвестен"
        factor = 2.54 if unit == 3 else 1
        return (x * factor if x is not None else None, y * factor if y is not None else None,
                "На сантиметр → DPI" if unit == 3 else "На дюйм")

    def validate_pixels(self, fields: dict, width: int, height: int, bits: tuple, samples: int, compression: int) -> None:
        pairs = [(273, 279), (324, 325)]
        found = False
        for offset_tag, count_tag in pairs:
            if offset_tag not in fields and count_tag not in fields:
                continue
            if offset_tag not in fields or count_tag not in fields:
                raise ParseError("Отсутствуют парные смещения / длины TIFF-полос или тайлов")
            offsets = self.values(fields[offset_tag])
            counts = self.values(fields[count_tag])
            if not offsets or len(offsets) != len(counts):
                raise ParseError("Массивы смещений и длин TIFF имеют разные размеры")
            for offset, count in zip(offsets, counts):
                if not isinstance(offset, int) or not isinstance(count, int) or offset < self.header_size or count <= 0:
                    raise ParseError("Некорректная полоса / тайл TIFF")
                self.reader.check_range(offset, count)
            found = True
            if offset_tag == 273:
                rows = self.scalar(fields, 278, height)
                planar = self.scalar(fields, 284, 1)
                if not isinstance(rows, int) or rows <= 0 or planar not in (1, 2):
                    raise ParseError("Неверные RowsPerStrip / PlanarConfiguration")
                strips_per_plane = (height + rows - 1) // rows
                if len(offsets) != strips_per_plane * (samples if planar == 2 else 1):
                    raise ParseError("Количество полос не соответствует геометрии TIFF")
                if compression == 1:
                    for i, count in enumerate(counts):
                        strip_rows = min(rows, height - (i % strips_per_plane) * rows)
                        bpp = bits[i // strips_per_plane] if planar == 2 else sum(bits)
                        if count < ((width * bpp + 7) // 8) * strip_rows:
                            raise ParseError("Несжатая полоса TIFF короче ожидаемого размера")
            else:
                tw, th = self.scalar(fields, 322), self.scalar(fields, 323)
                if not isinstance(tw, int) or not isinstance(th, int):
                    raise ParseError("Отсутствует геометрия тайлов TIFF")
                dimensions(tw, th)
                planar = self.scalar(fields, 284, 1)
                if planar not in (1, 2):
                    raise ParseError("Неверная PlanarConfiguration")
                per_plane = ((width + tw - 1) // tw) * ((height + th - 1) // th)
                if len(offsets) != per_plane * (samples if planar == 2 else 1):
                    raise ParseError("Количество тайлов не соответствует геометрии TIFF")
                if compression == 1:
                    for i, count in enumerate(counts):
                        bpp = bits[i // per_plane] if planar == 2 else sum(bits)
                        if count < ((tw * bpp + 7) // 8) * th:
                            raise ParseError("Несжатый тайл TIFF обрезан")
        if not found and compression == 6 and 513 in fields and 514 in fields:
            self.reader.check_range(self.scalar(fields, 513), self.scalar(fields, 514))
            found = True
        if not found:
            raise ParseError("В TIFF нет полос, тайлов или JPEGInterchangeFormat")


def parse(reader: BinaryReader, result: ImageMetadata) -> None:
    tiff = TiffDirectoryReader(reader)
    queue = [(tiff.first_ifd, True)]
    visited = set()
    pages = subimages = 0
    while queue:
        offset, main = queue.pop()
        if offset in visited:
            raise ParseError("Цикл / повторная ссылка в цепочке TIFF IFD")
        visited.add(offset)
        if len(visited) > 4096:
            raise ParseLimit("Слишком много TIFF IFD")
        fields, next_ifd = tiff.directory(offset)
        width, height = tiff.scalar(fields, 256), tiff.scalar(fields, 257)
        if not isinstance(width, int) or not isinstance(height, int):
            raise ParseError("Отсутствуют целочисленные ImageWidth / ImageLength")
        dimensions(width, height)
        samples = tiff.scalar(fields, 277, 1)
        if not isinstance(samples, int) or samples < 1 or samples > 64:
            raise ParseError("Неверный SamplesPerPixel")
        bits = tiff.values(fields[258], 64) if 258 in fields else (1,) * samples
        if len(bits) != samples or any(not isinstance(v, int) or v < 1 or v > 128 for v in bits):
            raise ParseError("BitsPerSample не соответствует SamplesPerPixel")
        compression = tiff.scalar(fields, 259, 1)
        xdpi, ydpi, resolution_unit = tiff.resolution(fields)
        tiff.validate_pixels(fields, width, height, bits, samples, compression)
        if pages == 0 and main:
            result.width, result.height = width, height
            result.bit_depth = sum(bits)
            result.dpi_x, result.dpi_y = xdpi, ydpi
            result.compression = COMPRESSION.get(compression, f"TIFF код {compression}")
            photo = tiff.scalar(fields, 262)
            result.details.update({"Контейнер": "BigTIFF" if tiff.big else "TIFF 6.0",
                                   "Порядок байтов": "Little-endian (II)" if tiff.endian == "<" else "Big-endian (MM)",
                                   "Бит на компоненты": ", ".join(map(str, bits)), "Компонентов": str(samples),
                                   "Цветовая модель": PHOTOMETRIC.get(photo, f"Код {photo}"),
                                   "Единица разрешения": resolution_unit,
                                   "Ориентация (тег 274)": str(tiff.scalar(fields, 274, 1)),
                                   "Тегов в первом IFD": str(len(fields))})
            if photo == 3:
                if 320 not in fields or fields[320].count != 3 * 2 ** bits[0]:
                    raise ParseError("Отсутствует или повреждена ColorMap TIFF")
                result.details["Цветов в палитре"] = str(2 ** bits[0])
        if main:
            pages += 1
        else:
            subimages += 1
        if 330 in fields:
            for suboffset in reversed(tiff.values(fields[330], 4096)):
                if suboffset:
                    queue.append((suboffset, False))
        if next_ifd:
            queue.append((next_ifd, main))
    result.details["Страниц"] = str(pages)
    result.details["Вложенных изображений"] = str(subimages)
    result.details["Показанные характеристики"] = "Первая страница TIFF"
