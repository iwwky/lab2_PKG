from .reader import BinaryReader, ParseError, ParseLimit, dimensions
from ..models import ImageMetadata


def skip_subblocks(reader: BinaryReader) -> None:
    count = 0
    while True:
        length = reader.u8()
        if length == 0:
            return
        reader.skip(length)
        count += 1
        if count > 2_000_000:
            raise ParseLimit("Слишком много GIF-подблоков")


def parse(reader: BinaryReader, result: ImageMetadata) -> None:
    version = reader.read(6)
    if version not in (b"GIF87a", b"GIF89a"):
        raise ParseError("Неверная сигнатура GIF")
    width, height, packed, background, aspect = reader.unpack("<HHBBB")
    dimensions(width, height)
    global_bits = (packed & 7) + 1
    global_colors = 2 ** global_bits if packed & 0x80 else 0
    if global_colors:
        if background >= global_colors:
            raise ParseError("Индекс фона выходит за глобальную палитру")
        reader.skip(global_colors * 3)
    result.width, result.height = width, height
    result.compression = "LZW (без потерь)"
    result.details.update({"Версия": version.decode(), "Глубина исходного цвета": str(((packed >> 4) & 7) + 1) + " бит на канал",
                           "Глобальная палитра": str(global_colors) + " цветов", "DPI": "Формат GIF не хранит абсолютный DPI"})
    if aspect:
        result.details["Соотношение сторон пикселя"] = f"{(aspect + 15) / 64:.4g}"
    frames = 0
    max_bits = 0
    blocks = 0
    while reader.tell() < reader.size:
        blocks += 1
        if blocks > 100_000:
            raise ParseLimit("Слишком много GIF-блоков")
        marker = reader.u8()
        if marker == 0x3b:
            if frames == 0:
                raise ParseError("GIF не содержит кадров")
            result.bit_depth = max_bits
            result.details["Кадров"] = str(frames)
            result.details["Глубина цвета"] = "Максимум бит индекса среди палитр кадров"
            if reader.tell() != reader.size:
                result.warnings.append("После GIF Trailer есть дополнительные байты")
            return
        if marker == 0x21:
            label = reader.u8()
            if label == 0xf9:
                if reader.u8() != 4:
                    raise ParseError("Неверная длина Graphic Control Extension")
                reader.skip(4)
                if reader.u8() != 0:
                    raise ParseError("Отсутствует терминатор Graphic Control Extension")
            else:
                skip_subblocks(reader)
        elif marker == 0x2c:
            left, top, fw, fh, fp = reader.unpack("<HHHHB")
            dimensions(fw, fh)
            if left + fw > width or top + fh > height:
                raise ParseError("Кадр GIF выходит за логический экран")
            bits = (fp & 7) + 1 if fp & 0x80 else global_bits
            if fp & 0x80:
                reader.skip(3 * 2 ** bits)
            elif not global_colors:
                raise ParseError("У кадра GIF нет палитры")
            lzw_bits = reader.u8()
            if lzw_bits < 2 or lzw_bits > 8:
                raise ParseError("Неверный минимальный размер кода LZW")
            skip_subblocks(reader)
            max_bits = max(max_bits, bits)
            frames += 1
        else:
            raise ParseError(f"Неизвестный маркер GIF: 0x{marker:02X}")
    raise ParseError("Отсутствует GIF Trailer (3B)")
