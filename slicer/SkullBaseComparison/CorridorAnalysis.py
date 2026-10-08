"""Slicer segmentation-to-engine adapter; heavy dependencies run externally."""
import json
import os
from pathlib import Path
import csv

import numpy as np
import qt
import slicer
import vtk


class CorridorAnalysis:
    """Keep this helper hidden when Slicer scans every Python file in the path."""

    def __init__(self, parent):
        parent.title = "Skull-base corridor analysis helper"
        parent.hidden = True


class CorridorAnalysisDialog(qt.QDialog):
    def __init__(self, planner):
        super().__init__(slicer.util.mainWindow())
        self.planner = planner
        self.root = Path(__file__).resolve().parents[2]
        self.process = None
        self.setWindowTitle("Compare segmented target access")
        self.resize(560, 650)
        layout = qt.QVBoxLayout(self)
        note = qt.QLabel(
            "Compare rigid instruments in supplied anatomy. No inferred surgical openings.\n"
            "Select target, protected anatomy, and optionally bone with reviewed removal masks.\n"
            "The UW atlas has incomplete ICA coverage; leave anatomy completeness unchecked."
        )
        note.wordWrap = True
        layout.addWidget(note)
        form = qt.QFormLayout()
        layout.addLayout(form)
        self.segments = {}
        for role, title in (
            ("target", "Target region"),
            ("protected", "Protected anatomy (union segment)"),
            ("bone", "Bone (optional)"),
            ("EEA", "EEA bone removal (optional)"),
            ("CTM", "CTM bone removal (optional)"),
        ):
            selector = slicer.qMRMLSegmentSelectorWidget()
            selector.noneEnabled = True
            selector.setMRMLScene(slicer.mrmlScene)
            selector.setCurrentNode(None)
            form.addRow(title, selector)
            self.segments[role] = selector
        self.length = qt.QDoubleSpinBox()
        self.length.setRange(1, 500)
        self.length.value = 150
        self.length.suffix = " mm"
        form.addRow("Instrument length", self.length)
        self.portal = qt.QDoubleSpinBox()
        self.portal.setRange(0.1, 100)
        self.portal.value = 8
        self.portal.suffix = " mm"
        form.addRow("Entry aperture diameter", self.portal)
        self.reviewer = qt.QLineEdit()
        form.addRow("Reviewer identity", self.reviewer)
        self.complete = qt.QCheckBox("I reviewed all required protected anatomy for this model")
        self.removals = qt.QCheckBox("I reviewed the selected bone-removal regions")
        layout.addWidget(self.complete)
        layout.addWidget(self.removals)
        self.python = qt.QLineEdit(os.environ.get(
            "SKULLBASE_PYTHON", ""))
        form.addRow("External engine Python", self.python)
        self.status = qt.QLabel("Full target grid; maximum 2,000 target voxels per analysis.")
        self.status.wordWrap = True
        layout.addWidget(self.status)
        self.run = qt.QPushButton("Run comparison")
        self.run.connect("clicked()", self.start)
        layout.addWidget(self.run)
        self.cancel = qt.QPushButton("Cancel analysis")
        self.cancel.enabled = False
        self.cancel.connect("clicked()", self.cancelAnalysis)
        layout.addWidget(self.cancel)
        self.output = None
        self.coverageNodes = []

    def array(self, role, volume, directory):
        selector = self.segments[role]
        node = selector.currentNode()
        segment_id = selector.currentSegmentID()
        if not node or not segment_id:
            return None
        if node.GetParentTransformNode():
            raise ValueError("Harden segmentation transforms before exporting to the engine.")
        array = slicer.util.arrayFromSegmentBinaryLabelmap(node, segment_id, volume)
        if tuple(reversed(array.shape)) != volume.GetImageData().GetDimensions():
            raise ValueError("Segment export did not preserve the full reference CT grid.")
        # Slicer arrays are KJI; the engine uses XYZ/IJK.
        array = np.ascontiguousarray(array.transpose(2, 1, 0), dtype=np.uint8)
        path = directory / (role + ".npy")
        np.save(path, array, allow_pickle=False)
        return str(path)

    def start(self, output_directory=None):
        try:
            if self.process and self.process.state() != qt.QProcess.NotRunning:
                return
            self.planner.updateMeasurements()
            if self.planner.report is None:
                raise ValueError("Define valid entry, target and ICA landmarks first.")
            volume = self.planner.volume.currentNode()
            if volume.GetParentTransformNode():
                raise ValueError("Harden CT transforms first; export requires a world-RAS grid.")
            python = Path(self.python.text)
            if not python.is_file():
                raise ValueError("Select the Python interpreter with the corridor package dependencies.")
            selected = output_directory or qt.QFileDialog.getExistingDirectory(
                self, "Choose an empty analysis output directory")
            if not selected:
                return
            directory = Path(selected)
            if any(directory.iterdir()):
                raise ValueError("Choose an empty output directory; previous results are preserved.")
            self.output = directory
            arrays = {role: self.array(role, volume, directory) for role in self.segments}
            if arrays["target"] is None:
                raise ValueError("Select an explicit target segment, not a placeholder landmark.")
            matrix = vtk.vtkMatrix4x4()
            volume.GetIJKToRASMatrix(matrix)
            affine = [[matrix.GetElement(i, j) for j in range(4)] for i in range(4)]
            target_point = self.planner.points("Target", 1)[0]
            exact_candidates = []
            for approach in ("EEA", "CTM"):
                entry_node = self.planner.selectors[approach + "Entry"].currentNode()
                candidate_id = (
                    entry_node.GetAttribute("SkullbaseProposal.CandidateID")
                    if entry_node else None
                )
                if candidate_id:
                    exact_candidates.append({
                        "candidate_id": candidate_id,
                        "approach_name": approach,
                        "entry_point_mm": self.planner.points(approach + "Entry", 1)[0],
                        "target_point_mm": target_point,
                    })
            request = {
                "target": arrays["target"], "bone": arrays["bone"],
                "protected": [{"name": "Selected protected anatomy", "path": arrays["protected"]}]
                if arrays["protected"] else [],
                "removals": {role: arrays[role] for role in ("EEA", "CTM") if arrays[role]},
                "affine": affine,
                "entries": {
                    "EEA": self.planner.points("EEAEntry", 1)[0],
                    "CTM": self.planner.points("CTMEntry", 1)[0],
                },
                "target_point": target_point,
                "exact_candidates": exact_candidates,
                "shaft_diameter_mm": self.planner.diameter.value,
                "portal_diameter_mm": self.portal.value,
                "instrument_length_mm": self.length.value,
                "anatomy_complete": self.complete.checked,
                "removals_reviewed": self.removals.checked,
                "reviewer": self.reviewer.text,
            }
            (directory / "request.json").write_text(json.dumps(request, indent=2))
            self.request = request
            self.process = qt.QProcess(self)
            environment = qt.QProcessEnvironment.systemEnvironment()
            # Slicer exports its embedded Python/Qt paths. They must not leak
            # into an independently installed numerical engine interpreter.
            for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH",
                        "QT_QPA_PLATFORM_PLUGIN_PATH", "LD_LIBRARY_PATH",
                        "DYLD_LIBRARY_PATH"):
                environment.remove(key)
            self.process.setProcessEnvironment(environment)
            self.process.setProcessChannelMode(qt.QProcess.MergedChannels)
            self.process.connect("finished(int,QProcess::ExitStatus)", self.finished)
            self.process.connect("errorOccurred(QProcess::ProcessError)", self.failed)
            self.run.enabled = False
            self.cancel.enabled = True
            self.status.text = "Computing sampled access…"
            self.process.start(str(python), [
                str(self.root / "research/run_slicer_bridge.py"),
                "--request", str(directory / "request.json"),
                "--output-dir", str(directory / "result"),
            ])
        except (ValueError, OSError, RuntimeError) as error:
            self.status.text = str(error)

    def failed(self, *_):
        self.run.enabled = True
        self.cancel.enabled = False
        self.status.text = "Engine process failed to start or crashed. Check the external interpreter."

    def finished(self, code, *_):
        self.run.enabled = True
        self.cancel.enabled = False
        raw = self.process.readAllStandardOutput().data()
        log = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        try:
            (self.output / "engine.log").write_text(log)
        except OSError as error:
            self.status.text = f"Could not save engine log: {error}"
            return
        if code:
            self.status.text = f"Analysis failed or cancelled. Details: {self.output / 'engine.log'}"
            return
        try:
            report = json.loads((self.output / "result/comparison.json").read_text())
            self.loadCoverage()
            counts = report["counts"]
            exact_paths = report.get("exact_paths", [])
            exact_summary = ""
            if exact_paths:
                exact_summary = "\nExact selected paths: " + ", ".join(
                    f"{path['candidate_id']}={path['state']}" for path in exact_paths
                )
                for path in exact_paths:
                    approach = path["approach_name"].upper()
                    shaft = self.planner.state.GetNodeReference("Shaft" + approach)
                    entry = self.planner.selectors[approach + "Entry"].currentNode()
                    for node in (shaft, entry):
                        if node:
                            node.SetAttribute("SkullbaseProposal.State", path["state"])
            self.status.text = (
                "Sampled target coverage (voxels)\n"
                f"EEA only: {counts['eea_only']}   CTM only: {counts['tm_only']}\n"
                f"Both: {counts['both']}   Not reached: {counts['not_reached']}\n"
                f"Unavailable: {counts['unavailable']}\n"
                f"{exact_summary}\n"
                "Finite sampling; no path found is not proof of no possible path.\n"
                f"Saved snapshot and reports: {self.output}\n"
                "Overlay is a saved analysis snapshot, not a live result."
            )
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            self.status.text = f"Engine finished; result display failed: {error}\nFiles: {self.output}"

    def loadCoverage(self):
        """Render the exported partition in the exact submitted world grid."""
        shape = np.load(self.request["target"], mmap_mode="r", allow_pickle=False).shape
        labels = np.zeros(shape, dtype=np.uint8)
        categories = (
            ("eea_only", "EEA only (sampled)", (0.23, 0.65, 1.0)),
            ("tm_only", "CTM only (sampled)", (0.94, 0.60, 0.24)),
            ("both", "Both (sampled)", (0.50, 0.80, 0.40)),
            ("not_reached", "Not reached at this sampling", (0.85, 0.25, 0.30)),
            ("unavailable", "Unavailable — missing evidence", (0.65, 0.65, 0.65)),
        )
        codes = {item[0]: index + 1 for index, item in enumerate(categories)}
        affine = np.asarray(self.request["affine"])
        with (self.output / "result/comparison_points.csv").open() as stream:
            for row in csv.DictReader(stream):
                point = np.array([float(row[axis + "_mm"]) for axis in ("x", "y", "z")])
                ijk = np.linalg.solve(affine[:3, :3], point - affine[:3, 3])
                index = np.rint(ijk).astype(int)
                if not np.allclose(ijk, index, atol=1e-5) or any(
                    index[i] < 0 or index[i] >= shape[i] for i in range(3)
                ):
                    raise ValueError("Exported coverage points do not match the submitted CT grid.")
                labels[tuple(index)] = codes[row["category"]]
        reference = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode")
        try:
            slicer.util.updateVolumeFromArray(reference, labels.transpose(2, 1, 0).copy())
            matrix = vtk.vtkMatrix4x4()
            for i in range(4):
                for j in range(4):
                    matrix.SetElement(i, j, affine[i, j])
            reference.SetIJKToRASMatrix(matrix)
            node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode", "Coverage snapshot — " + self.output.name)
            node.CreateDefaultDisplayNodes()
            node.SetReferenceImageGeometryParameterFromVolumeNode(reference)
            for index, (_, title, color) in enumerate(categories, 1):
                if not np.any(labels == index):
                    continue
                segment_id = node.GetSegmentation().AddEmptySegment("", title, color)
                slicer.util.updateSegmentBinaryLabelmapFromArray(
                    (labels == index).transpose(2, 1, 0).astype(np.uint8),
                    node, segment_id, reference)
            node.SetAttribute("SkullbaseComparison.OutputDirectory", str(self.output))
            node.GetDisplayNode().SetOpacity2DFill(0.25)
            node.CreateClosedSurfaceRepresentation()
            self.coverageNodes.append(node)
        finally:
            slicer.mrmlScene.RemoveNode(reference)

    def cancelAnalysis(self):
        if self.process:
            self.process.kill()

    def closeEvent(self, event):
        if self.process and self.process.state() != qt.QProcess.NotRunning:
            self.status.text = "Cancel the running analysis before closing."
            event.ignore()
            return
        event.accept()
