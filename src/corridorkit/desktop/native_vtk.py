"""Rate-limit Cocoa expose-triggered VTK renders to avoid a paint feedback loop."""
import time
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor


class PacedVTKInteractor(QVTKRenderWindowInteractor):
    def __init__(self, *args, **kwargs):
        self._last_paint = 0.0
        super().__init__(*args, **kwargs)

    def paintEvent(self, event):
        now = time.monotonic()
        if now - self._last_paint < 1 / 30:
            return
        self._last_paint = now
        super().paintEvent(event)
