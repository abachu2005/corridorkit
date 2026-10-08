"""Standalone Qt panel for sampled coverage; no analysis engine or app mutation."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from skullbase_corridor.analysis.coverage import (
    CATEGORY_LABELS, SAMPLING_CAVEAT, CoverageCategory, CoverageComparison,
    build_coverage_comparison,
)
from skullbase_corridor.domain.models import CaseResult, CorridorCase


class ComparisonPanel(QtWidgets.QWidget):
    """Call set_case on every configuration change, then set_result after analysis.

    set_case invalidates the prior result even for the same case ID. clear removes
    both inputs; set_result(None) removes only the result.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.case: CorridorCase | None = None
        self.result: CaseResult | None = None
        self.comparison: CoverageComparison | None = None
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("EEA / TM sampled target coverage")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        self.summary = QtWidgets.QLabel()
        self.summary.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Category", "Samples", "Volume (mm³)"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch,
        )
        for column in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.setMaximumHeight(220)
        layout.addWidget(self.table)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(140)
        layout.addWidget(self.details)
        self.clear()

    def set_case(self, case: CorridorCase | None) -> None:
        self.case = case
        self.result = None
        self._refresh()

    def set_result(self, result: CaseResult | None) -> None:
        # A result delivered without a case must never attach to a future case.
        self.result = result if self.case is not None else None
        self._refresh()

    def clear(self) -> None:
        self.case = None
        self.result = None
        self._refresh()

    def _refresh(self) -> None:
        if self.case is None:
            self.comparison = None
            self.table.setRowCount(0)
            self.summary.setText("Comparison unavailable — open a case. " + SAMPLING_CAVEAT)
            self.details.clear()
            return
        self.comparison = build_coverage_comparison(self.case, self.result)
        self.summary.setText(self.comparison.explanation)
        self.table.setRowCount(len(CoverageCategory))
        for row, category in enumerate(CoverageCategory):
            volume = self.comparison.volume_mm3
            values = (
                CATEGORY_LABELS[category], str(self.comparison.counts[category.value]),
                "—" if volume is None else f"{volume[category.value]:.6g}",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        self.details.setPlainText("\n".join(self.comparison.issues))
