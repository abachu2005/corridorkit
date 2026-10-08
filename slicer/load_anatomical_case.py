"""Load the inspected UW atlas into Slicer without inventing approach landmarks.

Run from Slicer's Python console with:
import runpy
runpy.run_path("/absolute/path/to/repo/slicer/load_anatomical_case.py")
"""
from pathlib import Path

import slicer

DEFAULT_ATLAS_DIRECTORY = Path(__file__).resolve().parents[1] / "data-cache/skullbase-atlas"


def load_atlas(directory=DEFAULT_ATLAS_DIRECTORY):
    directory = Path(directory)
    image_path = directory / "CT_template_pub - Neeraja Konuthula.nrrd"
    segments_path = directory / "Segmentation_atlas_pub.seg.nrrd"
    if not image_path.is_file() or not segments_path.is_file():
        raise FileNotFoundError("Prepare and inspect the public skull-base atlas first.")
    volume = slicer.util.loadVolume(str(image_path))
    segmentation = slicer.util.loadSegmentation(str(segments_path))
    if not volume or not segmentation:
        raise RuntimeError("Slicer could not load both atlas originals.")
    volume.SetName("UW averaged skull-base CT — NOT a patient tumor scan")
    segmentation.SetName("UW atlas labels — review coverage before measurements")
    segmentation.CreateClosedSurfaceRepresentation()
    segmentation.GetDisplayNode().SetOpacity3D(0.25)
    slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
    slicer.util.setSliceViewerLayers(background=volume)
    slicer.util.resetSliceViews()
    slicer.util.resetThreeDViews()
    return volume, segmentation


if __name__ in ("__main__", "<run_path>"):
    load_atlas()
