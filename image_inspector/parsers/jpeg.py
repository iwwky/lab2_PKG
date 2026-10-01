import io
import struct
from .reader import BinaryReader, ParseError, ParseLimit, ParseUnsupported, dimensions
from .tiff import TiffDirectoryReader
from ..models import ImageMetadata

SOF = {0xc0: "Baseline DCT", 0xc1: "Extended sequential DCT", 0xc2: "Progressive DCT",
       0xc3: "Lossless", 0xc5: "Differential sequential DCT", 0xc6: "Differential progressive DCT",
       0xc7: "Differential lossless", 0xc9: "Arithmetic sequential DCT", 0xca: "Arithmetic progressive DCT",
       0xcb: "Arithmetic lossless", 0xcd: "Differential arithmetic sequential DCT",
       0xce: "Differential arithmetic progressive DCT", 0xcf: "Differential arithmetic lossless"}


def exif_density(payload: bytes) -> tuple[float | None, float | None]:
    with io.BytesIO(payload) as stream:
        tiff = TiffDirectoryReader(BinaryReader(stream))
        fields, _ = tiff.directory(tiff.first_ifd)
        x, y, _ = tiff.resolution(fields)
        return x, y


def end_marker(reader: BinaryReader, start_of_scan: int, result: ImageMetadata) -> None:

    if reader.at(reader.size - 2, 2) == b"\xff\xd9":
        return

    end = reader.size
    inspected = 0
    while end > start_of_scan:
        begin = max(start_of_scan, end - 65536)
        tail = reader.at(begin, end - begin)
        index = tail.rfind(b"\xff\xd9")
        if index >= 0:
            result.warnings.append(f"После EOI есть {reader.size - begin - index - 2} дополнительных байт")
            return
        inspected += len(tail)
        if inspected >= 4 * 1024 * 1024 and begin > start_of_scan:
            raise ParseLimit("EOI не найден в последних 4 МБ; полный поиск пропущен")
        if begin == start_of_scan:
            break
        end = begin + 1
    raise ParseError("Отсутствует завершающий маркер JPEG EOI (FF D9)")


def parse(reader: BinaryReader, result: ImageMetadata) -> None:
    if reader.read(2) != b"\xff\xd8":
        raise ParseError("Неверная сигнатура JPEG SOI")
    sof = None
    jfif = (None, None)
    exif = (None, None)
    quantization = {}
    segments = 0
    while reader.tell() < reader.size:
        segments += 1
        if segments > 10000:
            raise ParseLimit("Слишком много JPEG-маркеров")
        if reader.u8() != 0xff:
            raise ParseError("Ожидался префикс JPEG-маркера FF")
        marker = reader.u8()
        while marker == 0xff:
            marker = reader.u8()
        if marker in (0, 0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            raise ParseError(f"Недопустимый маркер JPEG до SOS: FF {marker:02X}")
        if marker == 0x01:
            continue
        length, = reader.unpack(">H")
        if length < 2:
            raise ParseError("Длина JPEG-сегмента меньше 2")
        count = length - 2
        reader.check_range(reader.tell(), count)
        if marker in SOF or marker in (0xe0, 0xe1, 0xdb, 0xda):
            payload = reader.read(count)
        else:
            reader.skip(count)
            continue
        if marker in SOF:
            if sof is not None:
                raise ParseUnsupported("JPEG с несколькими SOF / иерархическим режимом не поддерживается")
            if len(payload) < 6:
                raise ParseError("Обрезанный SOF")
            precision, height, width, components = struct.unpack_from(">BHHB", payload)
            if height == 0:
                raise ParseUnsupported("JPEG с высотой в маркере DNL не поддерживается")
            if components > 4:
                raise ParseUnsupported("JPEG с более чем четырьмя компонентами не поддерживается")
            dimensions(width, height)
            if not 1 <= components <= 4 or len(payload) != 6 + 3 * components or not 2 <= precision <= 16:
                raise ParseError("Некорректные параметры SOF")
            if marker == 0xc0 and precision != 8:
                raise ParseError("Baseline JPEG требует 8 бит на компоненту")
            component_ids = set()
            for i in range(components):
                cid, sampling, table = payload[6 + i * 3:9 + i * 3]
                if cid in component_ids or not 1 <= sampling >> 4 <= 4 or not 1 <= sampling & 15 <= 4 or table > 3:
                    raise ParseError("Некорректная компонента JPEG SOF")
                component_ids.add(cid)
            result.width, result.height, result.bit_depth = width, height, precision * components
            result.compression = f"JPEG: {SOF[marker]}"
            result.details.update({"Бит на компоненту": str(precision), "Компонентов": str(components),
                                   "Глубина цвета": "Точность SOF × число компонент до экранного преобразования",
                                   "Маркер SOF": f"FF {marker:02X}"})
            sof = component_ids
        elif marker == 0xe0 and payload.startswith(b"JFIF\x00"):
            if len(payload) < 14:
                raise ParseError("Обрезан JFIF APP0")
            major, minor, unit, x, y, tx, ty = struct.unpack_from(">BBBHHBB", payload, 5)
            if len(payload) < 14 + 3 * tx * ty or unit not in (0, 1, 2):
                raise ParseError("Неверные параметры JFIF APP0")
            if unit in (1, 2):
                factor = 2.54 if unit == 2 else 1
                jfif = (x * factor if x else None, y * factor if y else None)
            result.details["JFIF"] = f"{major}.{minor:02d}; единица плотности {unit}"
            if unit == 0:
                result.details["Отношение плотностей JFIF"] = f"{x}:{y}, абсолютный DPI не задан"
        elif marker == 0xe1 and payload.startswith(b"Exif\x00\x00"):
            exif = exif_density(payload[6:])
            result.details["Exif"] = "TIFF IFD прочитан вручную"
        elif marker == 0xdb:
            position = 0
            while position < len(payload):
                description = payload[position]
                position += 1
                precision, table_id = description >> 4, description & 15
                if precision not in (0, 1) or table_id > 3:
                    raise ParseError("Неверная таблица квантования DQT")
                byte_count = 64 * (precision + 1)
                if position + byte_count > len(payload):
                    raise ParseError("Обрезанная таблица квантования DQT")
                values = struct.unpack_from(">" + ("64H" if precision else "64B"), payload, position)
                if 0 in values:
                    raise ParseError("Нулевая величина в таблице квантования JPEG")
                quantization[table_id] = values
                position += byte_count
        elif marker == 0xda:
            if sof is None or not payload:
                raise ParseError("SOS до SOF")
            components = payload[0]
            if not 1 <= components <= len(sof) or len(payload) != 4 + 2 * components:
                raise ParseError("Некорректная длина SOS")
            scan_ids = payload[1:1 + 2 * components:2]
            if len(set(scan_ids)) != components or any(cid not in sof for cid in scan_ids):
                raise ParseError("SOS ссылается на неизвестные / повторные компоненты")
            if reader.size - reader.tell() < 3:
                raise ParseError("Отсутствуют данные JPEG-скана")
            end_marker(reader, reader.tell(), result)
            result.dpi_x = exif[0] if exif[0] is not None else jfif[0]
            result.dpi_y = exif[1] if exif[1] is not None else jfif[1]
            result.details["Источник DPI"] = "Exif" if any(v is not None for v in exif) else "JFIF" if any(v is not None for v in jfif) else "Не указан"
            if all(v is not None for v in exif + jfif) and any(abs(a - b) > 0.5 for a, b in zip(exif, jfif)):
                result.warnings.append("DPI в Exif и JFIF различается; показан Exif")
            result.details["JPEG-сегментов до скана"] = str(segments)
            for table, values in sorted(quantization.items()):
                result.details[f"DQT {table} (зигзаг)"] = "\n".join(" ".join(f"{v:3}" for v in values[i:i + 8]) for i in range(0, 64, 8))
            return
    raise ParseError("Отсутствует JPEG SOS / данные изображения")
