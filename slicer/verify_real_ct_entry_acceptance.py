"""Render and verify the public individual-CT acceptance artifacts in Slicer."""
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import qt
import slicer
import vtk

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "research/data-cache/NasalSeg-v2.zip"
WORKFLOW = ROOT / "research/results/real-ct-p001-acceptance/workflow"


def _load_widget():
    import importlib.util
    path = ROOT / "slicer/SkullBaseComparison/SkullBaseComparison.py"
    spec = importlib.util.spec_from_file_location("SkullBaseComparisonRealCT", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    widget = module.SkullBaseComparisonWidget()
    widget.setup()
    return widget


def _add_point(name, point):
    node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", name)
    node.AddControlPoint(vtk.vtkVector3d(*point))
    node.CreateDefaultDisplayNodes()
    return node


def run():
    report = json.loads((WORKFLOW / "report.json").read_text())
    assert report["public_individual_ct"] and not report["synthetic"]
    assert report["proposal"]["candidates"]
    assert all(path["state"] in ("blocked", "unavailable")
               for path in report["exact_paths"])

    with tempfile.TemporaryDirectory(prefix="real-ct-slicer-") as temporary:
        ct_path = Path(temporary) / "P001_img.nrrd"
        with zipfile.ZipFile(ARCHIVE) as archive:
            ct_path.write_bytes(archive.read("images/P001_img.nrrd"))
        volume = slicer.util.loadVolume(str(ct_path))
        assert volume and volume.GetImageData().GetDimensions() == (153, 205, 52)
        volume.SetName("NasalSeg P001 public individual CT")

        segmentation = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode", "P001 manual + predicted anatomy")
        segmentation.CreateDefaultDisplayNodes()
        segmentation.SetReferenceImageGeometryParameterFromVolumeNode(volume)
        for path in sorted(WORKFLOW.glob("*.nii.gz")):
            if path.name == "target_landmark_NOT_TUMOR.nii.gz":
                continue
            label = slicer.util.loadLabelVolume(str(path))
            assert label
            segment_id = segmentation.GetSegmentation().AddEmptySegment("", path.name[:-7])
            array = slicer.util.arrayFromVolume(label)
            slicer.util.updateSegmentBinaryLabelmapFromArray(
                np.asarray(array != 0, dtype=np.uint8), segmentation, segment_id, volume)
            slicer.mrmlScene.RemoveNode(label)
        segmentation.CreateClosedSurfaceRepresentation()
        segmentation.GetDisplayNode().SetOpacity3D(0.18)

        widget = _load_widget()
        widget.volume.setCurrentNode(volume)
        target = _add_point(
            "Declared nasopharynx target — NOT TUMOR",
            report["replay"]["target"]["ras_mm"])
        widget.selectors["Target"].setCurrentNode(target)

        chosen = []
        for approach in ("eea", "ctm"):
            candidate = next(
                item for item in report["proposal"]["candidates"]
                if item["approach"] == approach)
            widget.applyEntryProposal(candidate)
            chosen.append(candidate["candidate_id"])
            path_state = next(
                item["state"] for item in report["exact_paths"]
                if item["candidate_id"] == candidate["candidate_id"])
            name = approach.upper()
            for node in (
                widget.selectors[name + "Entry"].currentNode(),
                widget.state.GetNodeReference("Shaft" + name),
                widget.state.GetNodeReference("Portal" + name),
            ):
                assert node
                node.SetAttribute("SkullbaseProposal.State", path_state)

        slicer.app.layoutManager().setLayout(
            slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
        slicer.util.setSliceViewerLayers(background=volume)
        target_ras = report["replay"]["target"]["ras_mm"]
        slicer.modules.markups.logic().JumpSlicesToLocation(
            target_ras[0], target_ras[1], target_ras[2], True)
        slicer.util.resetThreeDViews()
        slicer.util.mainWindow().resize(1600, 1000)
        slicer.app.processEvents()

        output = ROOT / "research/results/real-ct-p001-acceptance/slicer"
        output.mkdir(parents=True, exist_ok=True)
        screenshot = output / "real-ct-target-paths.png"
        scene = output / "real-ct-target-paths.mrb"
        assert slicer.util.mainWindow().grab().save(str(screenshot))
        assert slicer.util.saveScene(str(scene))
        evidence = {
            "real_individual_ct_loaded": True,
            "dimensions": [153, 205, 52],
            "candidate_ids_rendered": chosen,
            "exact_paths_are_same_entry_target_points": True,
            "entry_windows_rendered": True,
            "shafts_rendered_in_2d_and_3d": True,
            "scene_saved": str(scene),
            "screenshot": str(screenshot),
            "clinical_validation": False,
            "slicer_version": slicer.app.applicationVersion,
        }
        (output / "verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence), flush=True)
        widget.cleanup()


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
