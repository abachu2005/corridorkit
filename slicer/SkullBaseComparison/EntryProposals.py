"""Slicer dialog and JSON bridge for target-driven entry proposals.

The executable bridge deliberately has a small file-based contract so Slicer's
embedded Python does not need the package's SciPy/Pydantic dependencies.
"""
import argparse
import importlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

try:
    import qt
    import vtk

    import slicer
except ImportError:  # External Python only needs the bridge functions below.
    qt = slicer = vtk = None


MASK_ROLES = (
    ("left_nasal_cavity", "Left nasal cavity", True),
    ("right_nasal_cavity", "Right nasal cavity", True),
    ("left_maxillary_sinus", "Left maxillary sinus", True),
    ("right_maxillary_sinus", "Right maxillary sinus", True),
    ("bone", "Bone", True),
    ("hard_palate", "Hard palate (optional)", False),
    ("upper_teeth", "Upper teeth (optional)", False),
    ("left_orbit", "Left orbit (optional)", False),
    ("right_orbit", "Right orbit (optional)", False),
    ("protected", "ICA / protected union (optional)", False),
)


class EntryProposals:
    """Prevent this helper from appearing as a second scripted module."""

    def __init__(self, parent):
        parent.title = "Entry proposal helper"
        parent.hidden = True


def load_request(request_path):
    """Load the thin CLI contract and invoke the package proposal function."""
    request_path = Path(request_path)
    request = json.loads(request_path.read_text())
    if request.get("operation") != "propose_entries":
        raise ValueError("request operation must be 'propose_entries'")
    masks = {}
    for name, item in request.get("masks", {}).items():
        path = item["path"] if isinstance(item, dict) else item
        masks[name] = np.load(path, allow_pickle=False)
    module = importlib.import_module("corridorkit.anatomy.entry_proposals")
    parameters = request.get("parameters", {})
    return module.propose_entries(
        target_ras_mm=request["target_ras_mm"],
        masks=masks,
        affine=np.asarray(request["affine"], dtype=float),
        **parameters,
    )


def run_bridge(request_path, output_path):
    result = load_request(request_path)
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    Path(output_path).write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    return payload


class EntryProposalDialog(qt.QDialog if qt else object):
    """Export reviewed Slicer segments and display exact returned candidates."""

    def __init__(self, planner):
        super().__init__(slicer.util.mainWindow())
        self.planner = planner
        self.root = Path(__file__).resolve().parents[2]
        self.process = None
        self.workDirectory = None
        self.proposalResult = None
        self.anatomyObservers = []
        self.restoring = False
        self.setWindowTitle("Suggest target-driven entries")
        self.resize(640, 760)
        layout = qt.QVBoxLayout(self)
        note = qt.QLabel(
            "GEOMETRIC PROPOSALS — REVIEW REQUIRED\n"
            "The target landmark is required. ICA/protected anatomy is optional; "
            "without it, candidates are conditional. No candidate is approved."
        )
        note.wordWrap = True
        layout.addWidget(note)
        form = qt.QFormLayout()
        layout.addLayout(form)
        self.segments = {}
        for role, title, _required in MASK_ROLES:
            selector = slicer.qMRMLSegmentSelectorWidget()
            selector.noneEnabled = True
            selector.setMRMLScene(slicer.mrmlScene)
            selector.connect("currentNodeChanged(vtkMRMLNode*)", self.inputsChanged)
            selector.connect("currentSegmentChanged(QString)", self.inputsChanged)
            form.addRow(title, selector)
            self.segments[role] = selector
        self.python = qt.QLineEdit(os.environ.get("CORRIDORKIT_PYTHON", ""))
        form.addRow("External package Python", self.python)
        self.suggest = qt.QPushButton("Suggest entries")
        self.suggest.connect("clicked()", self.start)
        layout.addWidget(self.suggest)
        self.cancel = qt.QPushButton("Cancel")
        self.cancel.enabled = False
        self.cancel.connect("clicked()", self.cancelProposal)
        layout.addWidget(self.cancel)
        self.progress = qt.QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.value = 0
        layout.addWidget(self.progress)
        self.status = qt.QLabel("Select the common-grid anatomical segments.")
        self.status.wordWrap = True
        layout.addWidget(self.status)
        self.candidates = qt.QListWidget()
        self.candidates.connect("itemSelectionChanged()", self.candidateSelected)
        layout.addWidget(self.candidates)
        self.restoreReferences()

    def restoreReferences(self):
        self.restoring = True
        for role, _title, _required in MASK_ROLES:
            node = self.planner.state.GetNodeReference("Proposal_" + role)
            segment_id = self.planner.state.GetParameter("ProposalSegment_" + role)
            self.segments[role].setCurrentNode(node)
            if node and segment_id:
                self.segments[role].setCurrentSegmentID(segment_id)
        self.restoring = False
        self.observeAnatomy()

    def observeAnatomy(self):
        for node, tag in self.anatomyObservers:
            node.RemoveObserver(tag)
        self.anatomyObservers = []
        seen = set()
        for selector in self.segments.values():
            node = selector.currentNode()
            if node and node.GetID() not in seen:
                seen.add(node.GetID())
                for event in (
                    vtk.vtkCommand.ModifiedEvent,
                    slicer.vtkMRMLTransformableNode.TransformModifiedEvent,
                ):
                    self.anatomyObservers.append(
                        (node, node.AddObserver(event, self.anatomyModified)))

    def inputsChanged(self, *_):
        if self.restoring:
            return
        for role, _title, _required in MASK_ROLES:
            selector = self.segments[role]
            node = selector.currentNode()
            self.planner.state.SetNodeReferenceID(
                "Proposal_" + role, node.GetID() if node else None)
            self.planner.state.SetParameter(
                "ProposalSegment_" + role, selector.currentSegmentID() or "")
        self.observeAnatomy()
        self.invalidate("Anatomy selection changed; previous proposals are stale.")
        self.planner.inputsChanged()

    def anatomyModified(self, *_):
        self.invalidate("Anatomy changed; previous proposals are stale.")
        self.planner.updateMeasurements()

    def invalidate(self, message="Target or anatomy changed; previous proposals are stale."):
        self.proposalResult = None
        self.candidates.clear()
        self.status.text = message
        self.planner.invalidateEntryProposals()

    def targetMayHaveChanged(self):
        if not self.proposalResult:
            return
        node = self.planner.selectors["Target"].currentNode()
        expected = self.proposalResult.get("target_ras_mm")
        if node is None or node.GetNumberOfDefinedControlPoints() != 1:
            self.invalidate()
            return
        point = [0.0, 0.0, 0.0]
        node.GetNthControlPointPositionWorld(0, point)
        if expected is None or not np.allclose(point, expected, atol=1e-6, rtol=0):
            self.invalidate()

    def exportMask(self, role, volume, directory):
        selector = self.segments[role]
        node = selector.currentNode()
        segment_id = selector.currentSegmentID()
        if not node or not segment_id:
            return None
        if node.GetParentTransformNode():
            raise ValueError("Harden segmentation transforms before exporting proposals.")
        array = slicer.util.arrayFromSegmentBinaryLabelmap(node, segment_id, volume)
        if tuple(reversed(array.shape)) != volume.GetImageData().GetDimensions():
            raise ValueError(f"{role} did not export on the full reference CT grid.")
        xyz = np.ascontiguousarray(array.transpose(2, 1, 0), dtype=np.uint8)
        path = directory / (role + ".npy")
        np.save(path, xyz, allow_pickle=False)
        return str(path)

    def buildRequest(self, directory):
        volume = self.planner.volume.currentNode()
        if volume is None or volume.GetImageData() is None:
            raise ValueError("Select an anatomical CT first.")
        if volume.GetParentTransformNode():
            raise ValueError("Harden the CT transform before exporting proposals.")
        target = self.planner.points("Target", 1)[0]
        masks = {}
        missing = []
        for role, title, required in MASK_ROLES:
            path = self.exportMask(role, volume, directory)
            if path:
                masks[role] = {"path": path, "status": "reviewed"}
            elif required:
                missing.append(title)
        if missing:
            raise ValueError("Select required segments: " + ", ".join(missing))
        matrix = vtk.vtkMatrix4x4()
        volume.GetIJKToRASMatrix(matrix)
        affine = [[matrix.GetElement(i, j) for j in range(4)] for i in range(4)]
        return {
            "schema_version": 1,
            "operation": "propose_entries",
            "coordinate_frame": "RAS_mm",
            "array_axis_order": "XYZ_IJK",
            "target_ras_mm": target,
            "affine": affine,
            "masks": masks,
            "parameters": {"candidates_per_surface": 2},
        }

    def start(self, output_directory=None):
        try:
            if self.process and self.process.state() != qt.QProcess.NotRunning:
                return
            python = Path(self.python.text)
            if not python.is_file():
                raise ValueError("Select the external Python containing the corridor package.")
            self.invalidate("Exporting full-grid masks…")
            self.workDirectory = Path(output_directory or tempfile.mkdtemp(
                prefix="corridorkit-entry-proposals-"))
            self.workDirectory.mkdir(parents=True, exist_ok=True)
            request = self.buildRequest(self.workDirectory)
            request_path = self.workDirectory / "request.json"
            request_path.write_text(json.dumps(request, indent=2, allow_nan=False))
            self.outputPath = self.workDirectory / "proposals.json"
            self.process = qt.QProcess(self)
            environment = qt.QProcessEnvironment.systemEnvironment()
            for key in (
                "PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH",
                "QT_QPA_PLATFORM_PLUGIN_PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
            ):
                environment.remove(key)
            self.process.setProcessEnvironment(environment)
            self.process.setProcessChannelMode(qt.QProcess.MergedChannels)
            self.process.connect("finished(int,QProcess::ExitStatus)", self.finished)
            self.process.connect("errorOccurred(QProcess::ProcessError)", self.failed)
            self.suggest.enabled = False
            self.cancel.enabled = True
            self.progress.setRange(0, 0)
            self.status.text = "Computing geometric proposals…"
            self.process.start(str(python), [
                str(Path(__file__).resolve()), "--request", str(request_path),
                "--output", str(self.outputPath),
            ])
        except (ValueError, OSError, RuntimeError) as error:
            self.status.text = str(error)

    def finished(self, code, *_):
        self.suggest.enabled = True
        self.cancel.enabled = False
        self.progress.setRange(0, 1)
        self.progress.value = 1
        raw = self.process.readAllStandardOutput().data()
        log = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if code:
            self.status.text = "Proposal engine failed or was cancelled.\n" + log[-1000:]
            return
        try:
            self.proposalResult = json.loads(self.outputPath.read_text())
            self.populateCandidates()
        except (OSError, ValueError, KeyError) as error:
            self.status.text = f"Proposal output could not be loaded: {error}"

    def failed(self, *_):
        self.suggest.enabled = True
        self.cancel.enabled = False
        self.progress.setRange(0, 1)
        self.status.text = "External proposal process failed to start or crashed."

    def populateCandidates(self):
        self.candidates.clear()
        candidates = self.proposalResult.get("candidates", [])
        for candidate in candidates:
            state = "conditional" if candidate.get("conditional_constraints") else "proposed"
            title = (
                f"{candidate['candidate_id']} — {candidate['approach'].upper()} "
                f"{candidate['side']} — {state}"
            )
            item = qt.QListWidgetItem(title)
            item.setData(qt.Qt.UserRole, candidate["candidate_id"])
            self.candidates.addItem(item)
        overall = {
            "complete": "proposed",
            "conditional": "conditional",
            "abstained": "unavailable",
        }.get(self.proposalResult.get("status"), "unavailable")
        missing = self.proposalResult.get("missing_constraints", [])
        if not candidates:
            overall = "unavailable"
        self.status.text = (
            f"State: {overall}. {len(candidates)} review-required candidate(s). "
            "Selecting one places that exact candidate; it does not approve it."
            + (("\n" + "\n".join(missing)) if missing else "")
        )

    def candidateSelected(self):
        selected = self.candidates.selectedItems()
        if len(selected) != 1 or not self.proposalResult:
            return
        candidate_id = selected[0].data(qt.Qt.UserRole)
        candidate = next(
            item for item in self.proposalResult["candidates"]
            if item["candidate_id"] == candidate_id)
        self.planner.applyEntryProposal(candidate)

    def cancelProposal(self):
        if self.process and self.process.state() != qt.QProcess.NotRunning:
            self.process.kill()

    def closeEvent(self, event):
        if self.process and self.process.state() != qt.QProcess.NotRunning:
            self.status.text = "Cancel the running proposal before closing."
            event.ignore()
            return
        event.accept()

    def cleanup(self):
        self.cancelProposal()
        for node, tag in self.anatomyObservers:
            node.RemoveObserver(tag)
        self.anatomyObservers = []
        if self.workDirectory and self.workDirectory.name.startswith(
                "corridorkit-entry-proposals-"):
            shutil.rmtree(self.workDirectory, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    run_bridge(arguments.request, arguments.output)


if __name__ == "__main__":
    main()
