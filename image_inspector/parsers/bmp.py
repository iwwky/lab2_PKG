import struct
from .reader import BinaryReader, ParseError, ParseUnsupported, dimensions, dpi_from_meter
from ..models import ImageMetadata

COMPRESSIONS = {0: "BI_RGB (без сжатия)", 1: "BI_RLE8", 2: "BI_RLE4", 3: "BI_BITFIELDS",
                4: "BI_JPEG", 5: "BI_PNG", 6: "BI_ALPHABITFIELDS"}


def parse(reader: BinaryReader, result: ImageMetadata) -> None:
    signature, declared, _, _, pixels = reader.unpack("<2sIHHI")
    if signature != b"BM":
        raise ParseError("Неверная сигнатура BMP")
    if declared == 0 or declared > reader.size:
        raise ParseError("Размер BMP в заголовке превышает фактический или равен нулю")
    header_size, = reader.unpack("<I")
    if header_size == 12:
        width, height, planes, bits = reader.unpack("<HHHH")
        compression = image_bytes = used_colors = 0
        palette_entry = 3
        result.details["Заголовок"] = "BITMAPCOREHEADER (OS/2)"
    elif header_size in {40, 52, 56, 108, 124}:
        width, signed_height, planes, bits, compression, image_bytes, xppm, yppm, used_colors, _ = reader.unpack("<iiHHIIiiII")
        height = abs(signed_height)
        reader.skip(header_size - 40)
        palette_entry = 4
        result.dpi_x, result.dpi_y = dpi_from_meter(xppm), dpi_from_meter(yppm)
        result.details["Порядок строк"] = "Сверху вниз" if signed_height < 0 else "Снизу вверх"
        result.details["Заголовок DIB"] = f"{header_size} байт"
        if signed_height < 0 and compression not in (0, 3, 6):
            raise ParseError("Сжатый BMP не может иметь отрицательную высоту")
    elif header_size == 64:
        raise ParseUnsupported("Вариант BMP OS/2 2.x с DIB 64 байта не поддерживается")
    else:
        raise ParseError(f"Неизвестный размер заголовка DIB: {header_size}")
    dimensions(width, height)
    if planes != 1 or bits not in (0, 1, 4, 8, 16, 24, 32):
        raise ParseError("Неверное число плоскостей или бит BMP")
    if compression not in COMPRESSIONS or (bits == 0 and compression not in (4, 5)):
        raise ParseError("Неверный тип сжатия BMP")
    if compression == 1 and bits != 8 or compression == 2 and bits != 4:
        raise ParseError("Глубина цвета не соответствует RLE")
    if compression in (3, 6) and bits not in (16, 32):
        raise ParseError("BITFIELDS требует 16 или 32 бита")
    masks = (16 if compression == 6 else 12) if header_size == 40 and compression in (3, 6) else 0
    palette_colors = (used_colors or 2 ** bits) if 0 < bits <= 8 else used_colors
    if 0 < bits <= 8 and palette_colors > 2 ** bits:
        raise ParseError("Палитра BMP больше диапазона индексов")
    metadata_end = 14 + header_size + masks + palette_colors * palette_entry
    if pixels < metadata_end or pixels >= declared:
        raise ParseError("Некорректное смещение растровых данных BMP")
    reader.check_range(14 + header_size, masks + palette_colors * palette_entry)
    if compression in (0, 3, 6):
        stride = ((width * bits + 31) // 32) * 4
        expected_bytes = stride * height
        if pixels + expected_bytes > declared:
            raise ParseError("Растровые данные BMP обрезаны")
        if image_bytes and image_bytes < expected_bytes:
            raise ParseError("biSizeImage меньше вычисленного размера растра")
    elif image_bytes == 0:
        raise ParseError("У сжатого BMP не задан biSizeImage")
    if image_bytes and pixels + image_bytes > declared:
        raise ParseError("biSizeImage выходит за пределы BMP")
    result.width, result.height = width, height
    result.bit_depth = bits or None
    result.compression = COMPRESSIONS[compression]
    result.details["Смещение пикселей"] = f"{pixels} байт"
    if palette_colors:
        result.details["Цветов в палитре"] = str(palette_colors)
        result.details["Устройство палитры"] = "BGR" if palette_entry == 3 else "BGR + резервный байт"
    if declared < reader.size:
        result.warnings.append("Фактический размер больше bfSize")
