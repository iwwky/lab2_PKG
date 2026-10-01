from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ImageMetadata:
    path: str
    format: str = "—"
    width: int | None = None
    height: int | None = None
    bit_depth: int | None = None
    dpi_x: float | None = None
    dpi_y: float | None = None
    compression: str = "—"
    file_size: int = 0
    status: str = "OK"
    message: str = ""
    bytes_read: int = 0
    elapsed_ms: float = 0.0
    details: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def dimensions(self) -> str:
        return f"{self.width} × {self.height}" if self.width is not None else "—"

    @property
    def dpi(self) -> str:
        def display(value: float | None) -> str:
            return f"{value:.2f}".rstrip("0").rstrip(".") if value else "не указано"
        if self.dpi_x is None and self.dpi_y is None:
            return "не указано"
        return f"{display(self.dpi_x)} × {display(self.dpi_y)}"

    @property
    def is_error(self) -> bool:
        return self.status in {"Файл повреждён", "Ошибка доступа", "Не поддерживается", "Лимит проверки"}
