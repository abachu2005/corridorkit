"""Native synthetic verification for target-first entry proposals.

Run with Slicer --no-splash --python-script
slicer/verify_target_proposals_in_slicer.py. This is integration evidence only,
not anatomical or clinical validation.
"""
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import vtk

import slicer

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PYTHON = Path(os.environ.get("SKULLBASE_PYTHON", "/opt/anaconda3/bin/python3"))


def load_widget():
    path = ROOT / "slicer/SkullBaseComparison/SkullBaseComparison.py"
    spec = importlib.util.spec_from_file_location("SkullBaseComparison", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SkullBaseComparisonWidget()


def box(shape, i0, i1, j0, j1, k0, k1):
    result = np.zeros(shape, np.uint8)
    result[k0:k1, j0:j1, i0:i1] = 1
    return result


def run():
    assert ENGINE_PYTHON.is_file(), f"External package Python missing: {ENGINE_PYTHON}"
    widget = load_widget()
    try:
        shape = (25, 31, 37)  # Slicer KJI, deliberately unequal.
        ct = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLScalarVolumeNode", "Synthetic proposal CT — not anatomy")
        slicer.util.updateVolumeFromArray(ct, np.zeros(shape, np.int16))
        matrix = vtk.vtkMatrix4x4()
        affine = np.array([
            [1.1, 0, 0, -20], [0, 1.3, 0, -18],
            [0, 0, 1.7, -12], [0, 0, 0, 1],
        ])
        for row in range(4):
            for column in range(4):
                matrix.SetElement(row, column, affine[row, column])
        ct.SetIJKToRASMatrix(matrix)
        widget.volume.setCurrentNode(ct)
        target_ijk = np.array([18, 18, 13, 1.0])
        target_ras = (affine @ target_ijk)[:3]
        target = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "Synthetic target")
        target.AddControlPoint(vtk.vtkVector3d(*target_ras))
        widget.selectors["Target"].setCurrentNode(target)
        widget.updateMeasurements()
        assert widget.report is None
        assert "Suggest entries" in widget.summary.text

        masks = {
            "left_nasal_cavity": box(shape, 10, 14, 5, 15, 8, 17),
            "right_nasal_cavity": box(shape, 22, 26, 5, 15, 8, 17),
            "left_maxillary_sinus": box(shape, 5, 10, 7, 18, 7, 18),
            "right_maxillary_sinus": box(shape, 26, 31, 7, 18, 7, 18),
            "bone": box(shape, 0, 37, 18, 20, 0, 25),
        }
        segmentation = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode", "Synthetic masks — not anatomy")
        segmentation.CreateDefaultDisplayNodes()
        segmentation.SetReferenceImageGeometryParameterFromVolumeNode(ct)
        widget.openEntryProposals()
        dialog = widget.entryProposalDialog
        dialog.python.text = str(ENGINE_PYTHON)
        for role, mask in masks.items():
            segment_id = segmentation.GetSegmentation().AddEmptySegment("", role)
            slicer.util.updateSegmentBinaryLabelmapFromArray(
                mask, segmentation, segment_id, ct)
            dialog.segments[role].setCurrentNode(segmentation)
            dialog.segments[role].setCurrentSegmentID(segment_id)

        with tempfile.TemporaryDirectory(prefix="slicer-proposals-native-") as temporary:
            dialog.start(temporary)
            deadline = time.monotonic() + 120
            while not dialog.suggest.enabled and time.monotonic() < deadline:
                slicer.app.processEvents()
                time.sleep(0.01)
            assert dialog.suggest.enabled, "Proposal subprocess timed out"
            assert dialog.proposalResult is not None, dialog.status.text
            assert dialog.proposalResult["candidates"], dialog.proposalResult
            assert all(
                candidate.get("conditional_constraints")
                for candidate in dialog.proposalResult["candidates"]
            ), "Omitted protected anatomy must make every candidate conditional"
            first = dialog.proposalResult["candidates"][0]
            widget.applyEntryProposal(first)
            approach = first["approach"].upper()
            entry = widget.selectors[approach + "Entry"].currentNode()
            point = [0.0, 0.0, 0.0]
            entry.GetNthControlPointPositionWorld(0, point)
            np.testing.assert_allclose(point, first["entry_ras_mm"])
            assert entry.GetAttribute("SkullbaseProposal.State") == "conditional"
            assert widget.state.GetNodeReference("Shaft" + approach)
            assert widget.state.GetNodeReference("Portal" + approach)
            target.SetNthControlPointPositionWorld(
                0, *(target_ras + np.array([0.0, 0.0, 1.7])))
            assert dialog.proposalResult is None
            assert entry.GetAttribute("SkullbaseProposal.State") == "unavailable"
            assert not widget.state.GetNodeReference(
                "Portal" + approach).GetDisplayNode().GetVisibility()

        print(json.dumps({
            "slicer_target_proposals_passed": True,
            "full_grid_xyz_passed": True,
            "target_first_passed": True,
            "optional_protected_conditional_passed": True,
            "exact_candidate_placement_passed": True,
            "stale_invalidation_passed": True,
            "clinical_validation": False,
            "slicer_version": slicer.app.applicationVersion,
        }))
    finally:
        widget.cleanup()


try:
    run()
except Exception:  # noqa: BLE001 - native smoke must report every integration failure.
    import traceback
    traceback.print_exc()
    slicer.app.exit(1)
else:
    slicer.app.exit(0)
