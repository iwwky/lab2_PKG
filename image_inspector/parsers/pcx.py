import struct
from .reader import BinaryReader, ParseError, dimensions
from ..models import ImageMetadata


def parse(reader: BinaryReader, result: ImageMetadata) -> None:
    header = reader.read(128)
    manufacturer, version, encoding, bits = header[:4]
    xmin, ymin, xmax, ymax, hdpi, vdpi = struct.unpack_from("<6H", header, 4)
    planes = header[65]
    bytes_per_line, palette_info = struct.unpack_from("<HH", header, 66)
    if manufacturer != 10 or version not in (0, 2, 3, 4, 5) or encoding not in (0, 1):
        raise ParseError("Некорректный заголовок PCX")
    if bits not in (1, 2, 4, 8) or planes not in (1, 2, 3, 4):
        raise ParseError("Недопустимая глубина / число плоскостей PCX")
    width, height = xmax - xmin + 1, ymax - ymin + 1
    dimensions(width, height)
    minimum_line = (width * bits + 7) // 8
    if bytes_per_line < minimum_line or bytes_per_line % 2:
        raise ParseError("Некорректный BytesPerLine PCX")
    if reader.size <= 128:
        raise ParseError("Отсутствуют данные изображения PCX")
    data_end = reader.size
    palette = "16 цветов в заголовке" if bits * planes <= 4 else "Нет"
    if bits == 8 and planes == 1 and version == 5:
        if reader.size < 128 + 1 + 769 or reader.at(reader.size - 769, 1) != b"\x0c":

            if palette_info != 2:
                raise ParseError("Отсутствует 256-цветная палитра PCX")
            palette = "Оттенки серого, без VGA-палитры"
        else:
            data_end -= 769
            palette = "256 цветов в конце файла (RGB)"
    raw_bytes = bytes_per_line * planes * height
    available = data_end - 128
    if encoding == 0 and available < raw_bytes:
        raise ParseError("Несжатые данные PCX обрезаны")


    if encoding == 1 and available < (2 * raw_bytes + 62) // 63:
        raise ParseError("Данные PCX слишком короткие даже для максимального RLE")
    result.width, result.height = width, height
    result.bit_depth = bits * planes
    result.dpi_x, result.dpi_y = hdpi or None, vdpi or None
    result.compression = "RLE (без потерь)" if encoding == 1 else "Без сжатия"
    result.details.update({"Версия PCX": str(version), "Плоскостей": str(planes), "Бит на плоскость": str(bits),
                           "Байт на строку плоскости": str(bytes_per_line), "Палитра": palette})
