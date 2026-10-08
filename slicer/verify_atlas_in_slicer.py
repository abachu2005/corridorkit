"""Native atlas load/render smoke; does not invent landmarks or operative routes."""
import json
from pathlib import Path
import runpy

import qt
import slicer

ROOT = Path(__file__).resolve().parents[1]


def run():
    loader = runpy.run_path(str(ROOT / "slicer/load_anatomical_case.py"), run_name="atlas_smoke")
    volume, segmentation = loader["load_atlas"]()
    assert volume.GetImageData().GetDimensions() == (367, 449, 304)
    assert segmentation.GetSegmentation().GetNumberOfSegments() == 14
    slicer.util.selectModule("SkullBaseComparison")
    widget = slicer.modules.skullbasecomparison.widgetRepresentation().self()
    widget.volume.setCurrentNode(volume)
    assert widget.report is None  # Atlas alone must never fabricate an operative plan.
    slicer.util.setSliceViewerLayers(background=volume)
    volume.GetDisplayNode().AutoWindowLevelOff()
    volume.GetDisplayNode().SetWindowLevel(2000, 400)
    slicer.util.setSliceViewerLayers(foreground=None)
    slicer.util.resetSliceViews()
    slicer.util.resetThreeDViews()
    slicer.util.mainWindow().resize(1440, 960)
    slicer.app.processEvents()
    directory = ROOT / "research/slicer-verification"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "atlas-native.png"
    assert slicer.util.mainWindow().grab().save(str(path)), "Screenshot save failed"
    report = {
        "atlas_load_passed": True,
        "module_discovery_passed": True,
        "dimensions": list(volume.GetImageData().GetDimensions()),
        "segments": segmentation.GetSegmentation().GetNumberOfSegments(),
        "slicer_version": slicer.app.applicationVersion,
        "screenshot": str(path),
        "anatomical_route_validation": False,
    }
    (directory / "atlas-native.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


def finish():
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        slicer.app.exit(1)
    else:
        slicer.app.exit(0)


qt.QTimer.singleShot(1500, finish)
