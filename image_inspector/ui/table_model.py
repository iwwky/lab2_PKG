from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from ..models import ImageMetadata

HEADERS = ["Файл", "Формат", "Размер, px", "DPI (X × Y)", "Бит/пиксель", "Сжатие", "Статус"]


class MetadataTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[ImageMetadata] = []
        self.sort_keys = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section] if orientation == Qt.Orientation.Horizontal else section + 1
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        result = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (result.name, result.format, result.dimensions, result.dpi,
                    str(result.bit_depth) if result.bit_depth is not None else "—",
                    result.compression, result.status)[col]
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{result.path}\n{result.message}".rstrip()
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (1, 2, 3, 4):
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def add(self, batch):
        if not batch:
            return
        first = len(self.rows)
        self.beginInsertRows(QModelIndex(), first, first + len(batch) - 1)
        self.rows.extend(batch)
        self.sort_keys.extend((r.name.casefold(), r.format, (r.width or 0) * (r.height or 0),
                               r.dpi_x or 0.0, r.bit_depth or 0, r.compression, r.status) for r in batch)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self.rows.clear()
        self.sort_keys.clear()
        self.endResetModel()

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if column < 0 or not self.rows:
            return


        persistent = self.persistentIndexList()
        identities = [(id(self.rows[index.row()]), index.column()) for index in persistent]
        self.layoutAboutToBeChanged.emit()
        indices = sorted(range(len(self.rows)), key=lambda i: self.sort_keys[i][column],
                         reverse=order == Qt.SortOrder.DescendingOrder)
        self.rows = [self.rows[i] for i in indices]
        self.sort_keys = [self.sort_keys[i] for i in indices]
        if persistent:
            positions = {id(row): i for i, row in enumerate(self.rows)}
            self.changePersistentIndexList(persistent, [self.index(positions[identity], col) for identity, col in identities])
        self.layoutChanged.emit()
