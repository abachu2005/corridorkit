import importlib.util
import pytest


@pytest.fixture(autouse=True)
def confirm_discard_for_automated_gui(monkeypatch):
    if importlib.util.find_spec("PySide6") is not None:
        from PySide6 import QtWidgets
        monkeypatch.setattr(
            QtWidgets.QMessageBox, "question",
            lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Discard,
        )
