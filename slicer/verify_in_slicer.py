"""Run with Slicer --no-splash --python-script slicer/verify_in_slicer.py.

Synthetic landmarks verify integration only, not surgical anatomy.
"""
import importlib.util
import csv
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np
import slicer
import vtk
from vtk.util.numpy_support import vtk_to_numpy

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PYTHON = Path(os.environ.get("CORRIDORKIT_PYTHON", "/opt/anaconda3/bin/python3"))
CATEGORIES = ("eea_only", "tm_only", "both", "not_reached", "unavailable")
TITLES = (
    "EEA only (sampled)", "CTM only (sampled)", "Both (sampled)",
    "Not reached at this sampling", "Unavailable — missing evidence",
)


def load_widget_module():
    module_path = ROOT / "slicer/SkullBaseComparison/SkullBaseComparison.py"
    spec = importlib.util.spec_from_file_location("SkullBaseComparison", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_invalidated(widget):
    assert widget.report is None
    assert not widget.exportButton.enabled
    assert all(not node.GetDisplayNode().GetVisibility() for node in widget.models.values())


def verify_landmarks(widget):
    ct = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode", "Synthetic test only")
    slicer.util.updateVolumeFromArray(ct, np.zeros((64, 64, 64), np.int16))
    widget.volume.setCurrentNode(ct)
    values = {
        "EEAEntry": [[0, 10, 0]],
        "CTMEntry": [[10, 0, 0]],
        "Target": [[0, 0, 0]],
        "ICAAxis": [[0, 0, 0], [10, 0, 0]],
    }
    for role, _, kind, _ in widget.ROLES:
        if role not in values:
            continue
        node = slicer.mrmlScene.AddNewNodeByClass(kind, role)
        for point in values[role]:
            node.AddControlPoint(vtk.vtkVector3d(*point))
        widget.selectors[role].setCurrentNode(node)
    widget.updateMeasurements()
    assert widget.report["angle_advantage_deg"] == 90
    assert len(widget.models) == 2
    widget.showVolume()
    # Moving a landmark must update the report, not leave stale measurements.
    widget.selectors["CTMEntry"].currentNode().SetNthControlPointPosition(0, 10, 10, 0)
    assert abs(widget.report["ctm_angle_deg"] - 45) < 1e-8
    target = widget.selectors["Target"].currentNode()
    target.RemoveAllControlPoints()
    assert_invalidated(widget)
    target.AddControlPoint(vtk.vtkVector3d(0, 0, 0))
    assert widget.report is not None and widget.exportButton.enabled
    target.SetNthControlPointPosition(0, 1000, 0, 0)
    assert_invalidated(widget)
    target.SetNthControlPointPosition(0, 0, 0, 0)
    target.AddControlPoint(vtk.vtkVector3d(1, 1, 1))
    assert_invalidated(widget)
    target.RemoveNthControlPoint(1)
    assert widget.report is not None
    widget.volume.setCurrentNode(None)
    assert_invalidated(widget)
    widget.volume.setCurrentNode(ct)
    assert widget.report is not None
    return ct


def vtk_matrix(affine):
    matrix = vtk.vtkMatrix4x4()
    for i in range(4):
        for j in range(4):
            matrix.SetElement(i, j, float(affine[i, j]))
    return matrix


def ras(affine, ijk):
    return (affine @ np.array([*ijk, 1.0]))[:3]


def synthetic_segments(widget, ct):
    # Unequal dimensions, anisotropic spacing, rotation, and translation expose
    # KJI/IJK transposition, axis flips, and accidental identity-affine exports.
    shape = (13, 19, 23)  # Slicer KJI
    affine = np.array([
        [0, -1.5, 0, 32], [1.25, 0, 0, -24],
        [0, 0, 2, 8], [0, 0, 0, 1],
    ], dtype=float)
    slicer.util.updateVolumeFromArray(ct, np.zeros(shape, np.int16))
    ct.SetIJKToRASMatrix(vtk_matrix(affine))
    ct.SetName("Synthetic rotated anisotropic CT — not anatomy")
    landmark_ijk = {
        "EEAEntry": [(10, 2, 6)], "CTMEntry": [(2, 10, 6)],
        "Target": [(10, 10, 6)], "ICAAxis": [(10, 10, 6), (15, 10, 6)],
    }
    for role, indices in landmark_ijk.items():
        node = widget.selectors[role].currentNode()
        node.RemoveAllControlPoints()
        for ijk in indices:
            node.AddControlPoint(vtk.vtkVector3d(*ras(affine, ijk)))
    widget.diameter.value = 0.5
    widget.updateMeasurements()
    assert widget.report is not None
    masks = {role: np.zeros(shape, np.uint8)
             for role in ("target", "protected", "bone", "EEA", "CTM")}
    masks["target"][6, 10, 10:12] = 1  # Only two engine target voxels.
    masks["protected"][1:3, 16:18, 19:21] = 1
    masks["bone"][:, 6, :] = 1
    masks["EEA"][3:10, 6, 6:16] = 1  # Reviewed aperture through the slab.
    masks.pop("CTM")  # This approach stays on the target side of the slab.
    segmentation = slicer.mrmlScene.AddNewNodeByClass(
        "vtkMRMLSegmentationNode", "Synthetic input masks — not anatomy")
    segmentation.CreateDefaultDisplayNodes()
    segmentation.SetReferenceImageGeometryParameterFromVolumeNode(ct)
    widget.openCorridorAnalysis()
    dialog = widget.corridorDialog
    widget.openCorridorAnalysis()
    assert widget.corridorDialog is dialog, "Opening twice must reuse the dialog"
    for role, mask in masks.items():
        segment_id = segmentation.GetSegmentation().AddEmptySegment("", role)
        slicer.util.updateSegmentBinaryLabelmapFromArray(mask, segmentation, segment_id, ct)
        dialog.segments[role].setCurrentNode(segmentation)
        dialog.segments[role].setCurrentSegmentID(segment_id)
    # Make optional absence explicit rather than relying on selector defaults.
    dialog.segments["CTM"].setCurrentNode(None)
    return dialog, segmentation, masks, affine


def export_request(widget, dialog, ct, masks, affine, directory):
    directory.mkdir()
    paths = {role: dialog.array(role, ct, directory) for role in dialog.segments}
    assert paths["CTM"] is None
    for role, mask in masks.items():
        exported = np.load(paths[role], allow_pickle=False)
        assert exported.dtype == np.uint8 and exported.flags.c_contiguous
        assert exported.shape == (23, 19, 13)
        np.testing.assert_array_equal(exported, mask.transpose(2, 1, 0))
    matrix = vtk.vtkMatrix4x4()
    ct.GetIJKToRASMatrix(matrix)
    submitted_affine = [[matrix.GetElement(i, j) for j in range(4)] for i in range(4)]
    np.testing.assert_allclose(submitted_affine, affine)
    return {
        "target": paths["target"], "bone": paths["bone"],
        "protected": [{"name": "Synthetic distant protected mask", "path": paths["protected"]}],
        "removals": {"EEA": paths["EEA"]}, "affine": submitted_affine,
        "entries": {role: widget.points(role + "Entry", 1)[0] for role in ("EEA", "CTM")},
        "target_point": widget.points("Target", 1)[0],
        "shaft_diameter_mm": widget.diameter.value, "portal_diameter_mm": 2.0,
        "instrument_length_mm": 50.0, "anatomy_complete": True,
        "removals_reviewed": True, "reviewer": "Synthetic integration test only",
        "sampling": {
            "polar_steps": 1, "azimuth_steps": 4, "target_directed": True,
            "adaptive_levels": 0, "max_target_witnesses": 2,
            "max_refinement_directions": 0, "portal_offset_rings": 0,
        },
    }


def assert_coverage(dialog, ct, affine, rows):
    before = len(dialog.coverageNodes)
    labelmaps_before = slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLLabelMapVolumeNode")
    dialog.loadCoverage()
    assert len(dialog.coverageNodes) == before + 1
    assert slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLLabelMapVolumeNode") == labelmaps_before
    node = dialog.coverageNodes[-1]
    assert node.GetAttribute("SkullbaseComparison.OutputDirectory") == str(dialog.output)
    assert np.isclose(node.GetDisplayNode().GetOpacity2DFill(), 0.25)
    assert node.GetDisplayNode().GetVisibility()
    segmentation = node.GetSegmentation()
    assert segmentation.GetNumberOfSegments() == len({row["category"] for row in rows})
    for category, title in zip(CATEGORIES, TITLES):
        expected = np.zeros(slicer.util.arrayFromVolume(ct).shape, np.uint8)
        expected_ras = []
        for row in rows:
            if row["category"] != category:
                continue
            point = np.array([float(row[axis + "_mm"]) for axis in ("x", "y", "z")])
            ijk = np.linalg.solve(affine[:3, :3], point - affine[:3, 3])
            np.testing.assert_allclose(ijk, np.rint(ijk), atol=1e-5)
            i, j, k = np.rint(ijk).astype(int)
            expected[k, j, i] = 1
            expected_ras.append(point)
        segment_id = segmentation.GetSegmentIdBySegmentName(title)
        if not expected_ras:
            assert not segment_id
            continue
        np.testing.assert_array_equal(
            slicer.util.arrayFromSegmentBinaryLabelmap(node, segment_id, ct), expected)
        # Inspect the segment's own oriented geometry, not only a resampled array.
        image = slicer.vtkOrientedImageData()
        assert node.GetBinaryLabelmapRepresentation(segment_id, image)
        image_matrix = vtk.vtkMatrix4x4()
        image.GetImageToWorldMatrix(image_matrix)
        internal = vtk_to_numpy(image.GetPointData().GetScalars()).reshape(
            tuple(reversed(image.GetDimensions())))
        extent = image.GetExtent()
        actual_ras = [
            image_matrix.MultiplyPoint((i + extent[0], j + extent[2], k + extent[4], 1))[:3]
            for k, j, i in np.argwhere(internal != 0)
        ]
        np.testing.assert_allclose(
            sorted(map(tuple, actual_ras)), sorted(map(tuple, expected_ras)), atol=1e-5)
        assert segmentation.GetSegment(segment_id).GetRepresentation("Closed surface") is not None
    return node


def verify_fake_coverage(dialog, ct, request, affine, directory):
    directory.mkdir()
    result = directory / "result"
    result.mkdir()
    indices = [(3, 4, 2), (7, 8, 3), (11, 12, 5), (16, 14, 8), (20, 16, 10)]
    target = np.zeros((23, 19, 13), np.uint8)
    rows = []
    for category, ijk in zip(CATEGORIES, indices):
        target[ijk] = 1
        point = ras(affine, ijk)
        rows.append(dict(zip(("x_mm", "y_mm", "z_mm"), point), category=category))
    target_path = directory / "target.npy"
    np.save(target_path, target, allow_pickle=False)
    dialog.request = dict(request, target=str(target_path))
    dialog.output = directory
    with (result / "comparison_points.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("x_mm", "y_mm", "z_mm", "category"))
        writer.writeheader()
        writer.writerows(rows)
    (result / "comparison.json").write_text(json.dumps({
        "target_count": 5, "counts": {category: 1 for category in CATEGORIES},
        "categories": list(CATEGORIES),
    }))
    assert_coverage(dialog, ct, affine, rows)


def verify_engine(dialog, ct, request, affine, directory):
    assert ENGINE_PYTHON.is_file(), f"External engine interpreter missing: {ENGINE_PYTHON}"
    bridge = ROOT / "research/run_slicer_bridge.py"
    assert bridge.is_file(), f"Slicer bridge entry point missing: {bridge}"
    directory.mkdir()
    request_path = directory / "request.json"
    request_path.write_text(json.dumps(request, indent=2, allow_nan=False))
    # Match CorridorAnalysis: isolate the engine from Slicer's embedded runtime.
    environment = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH",
                "QT_QPA_PLATFORM_PLUGIN_PATH", "LD_LIBRARY_PATH",
                "DYLD_LIBRARY_PATH"):
        environment.pop(key, None)
    completed = subprocess.run(
        [str(ENGINE_PYTHON), str(bridge), "--request", str(request_path),
         "--output-dir", str(directory / "result")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120, check=False,
        env=environment,
    )
    assert completed.returncode == 0, (
        f"Bridge exited {completed.returncode}\n{completed.stdout}\n{completed.stderr}")
    report = json.loads((directory / "result/comparison.json").read_text())
    assert report["target_count"] == 2
    assert set(report["counts"]) == set(CATEGORIES)
    assert sum(report["counts"].values()) == 2
    assert report["counts"]["unavailable"] == 0, report
    assert sum(report["counts"][key] for key in ("eea_only", "tm_only", "both")) > 0, report
    with (directory / "result/comparison_points.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    points = [tuple(float(row[axis + "_mm"]) for axis in ("x", "y", "z")) for row in rows]
    np.testing.assert_allclose(
        sorted(points), sorted(tuple(ras(affine, ijk)) for ijk in ((10, 10, 6), (11, 10, 6))))
    for category in CATEGORIES:
        assert sum(row["category"] == category for row in rows) == report["counts"][category]
    dialog.request = request
    dialog.output = directory
    assert_coverage(dialog, ct, affine, rows)
    return report["counts"]


def verify_dialog_process(dialog, directory):
    """Exercise the real asynchronous UI export/process/completion path."""
    directory.mkdir()
    dialog.python.text = str(ENGINE_PYTHON)
    dialog.length.value = 50
    dialog.portal.value = 2
    dialog.complete.checked = True
    dialog.removals.checked = True
    dialog.reviewer.text = "Synthetic integration test only"
    before = len(dialog.coverageNodes)
    dialog.start(str(directory))
    assert dialog.process is not None, dialog.status.text
    deadline = time.monotonic() + 120
    while not dialog.run.enabled and time.monotonic() < deadline:
        slicer.app.processEvents()
        time.sleep(0.01)
    if not dialog.run.enabled:
        dialog.cancelAnalysis()
        raise AssertionError("Asynchronous dialog analysis exceeded 120 seconds")
    assert len(dialog.coverageNodes) == before + 1, dialog.status.text
    assert "Sampled target coverage" in dialog.status.text, dialog.status.text
    assert (directory / "engine.log").is_file()


def verify_scene_round_trip(module, widget, ct, directory):
    expected = {role: widget.points(role, count) for role, _, _, count in widget.ROLES
                if widget.selectors[role].currentNode()}
    diameter = widget.diameter.value
    expected_affine = vtk.vtkMatrix4x4()
    ct.GetIJKToRASMatrix(expected_affine)
    expected_name = ct.GetName()
    scene_path = directory / "synthetic-verification.mrb"
    try:
        assert slicer.util.saveScene(str(scene_path)), "Failed to save synthetic scene"
    finally:
        widget.cleanup()
        slicer.app.processEvents()
    slicer.mrmlScene.Clear(0)
    assert slicer.util.loadScene(str(scene_path)), "Failed to reopen synthetic scene"
    slicer.app.processEvents()
    # Reopening the module after loading exercises persisted references without
    # injecting a new empty parameter node into the scene before import.
    restored = module.SkullBaseComparisonWidget()
    try:
        assert restored.volume.currentNode() is not None
        assert restored.volume.currentNode().GetName() == expected_name
        assert restored.state.GetNodeReference("CT") == restored.volume.currentNode()
        actual_affine = vtk.vtkMatrix4x4()
        restored.volume.currentNode().GetIJKToRASMatrix(actual_affine)
        for i in range(4):
            for j in range(4):
                assert np.isclose(actual_affine.GetElement(i, j), expected_affine.GetElement(i, j))
        assert np.isclose(restored.diameter.value, diameter)
        for role, points in expected.items():
            assert restored.state.GetNodeReference(role) == restored.selectors[role].currentNode()
            np.testing.assert_allclose(restored.points(role, len(points)), points)
        assert restored.report is not None and restored.exportButton.enabled
        assert len(restored.models) == 2
        for role, model in restored.models.items():
            assert restored.state.GetNodeReference("Shaft" + role) == model
            assert model.GetDisplayNode().GetVisibility()
        restored.selectors["Target"].currentNode().RemoveAllControlPoints()
        assert_invalidated(restored)
    finally:
        restored.cleanup()


def run():
    module = load_widget_module()
    widget = module.SkullBaseComparisonWidget()
    cleaned = False
    try:
        with tempfile.TemporaryDirectory(prefix="corridorkit-slicer-verification-") as temporary:
            directory = Path(temporary)
            ct = verify_landmarks(widget)
            dialog, _, masks, affine = synthetic_segments(widget, ct)
            request = export_request(widget, dialog, ct, masks, affine, directory / "exports")
            verify_fake_coverage(dialog, ct, request, affine, directory / "fake")
            counts = verify_engine(dialog, ct, request, affine, directory / "engine")
            verify_dialog_process(dialog, directory / "dialog")
            # The round-trip helper takes ownership of cleaning up the original widget.
            cleaned = True
            verify_scene_round_trip(module, widget, ct, directory)
        report = {
            "slicer_integration_passed": True, "landmark_invalidation_passed": True,
            "kji_to_ijk_and_ras_passed": True, "five_category_overlay_passed": True,
            "external_engine_passed": True, "engine_counts": counts,
            "scene_reference_round_trip_passed": True, "anatomical_validation": False,
            "asynchronous_dialog_passed": True,
            "slicer_version": slicer.app.applicationVersion,
        }
        evidence = ROOT / "research/slicer-verification"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "integration.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    finally:
        if not cleaned:
            widget.cleanup()


try:
    run()
except Exception:
    import traceback
    traceback.print_exc()
    slicer.app.exit(1)
else:
    slicer.app.exit(0)
