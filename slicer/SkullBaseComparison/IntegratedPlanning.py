"""One-click local-first planning controller for SkullBaseComparison."""

import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import qt
import slicer
import vtk


class IntegratedPlanningController(qt.QObject):
    def __init__(self, planner):
        super().__init__(planner.parent)
        self.planner = planner
        self.process = None
        self.directory = None
        self.result = None

    def configured(self):
        return (
            self.planner.inferenceBackend.currentIndex == 0
            or bool(self.planner.endpointUrl.text and self.planner.endpointToken.text)
        )

    def start(self):
        if self.process and self.process.state() != qt.QProcess.NotRunning:
            return
        try:
            volume = self.planner.volume.currentNode()
            if volume is None or volume.GetImageData() is None:
                raise ValueError("Select an anatomical CT first.")
            if volume.GetParentTransformNode():
                raise ValueError("Harden the CT transform before planning.")
            target = self.planner.points("Target", 1)[0]
            python = self.planner.externalPython()
            if not python.is_file():
                raise ValueError("The managed planning runtime is not installed.")
            if not self.configured():
                raise ValueError("Configure the optional Azure endpoint and token.")
            self.cancel(remove=False)
            self.directory = Path(tempfile.mkdtemp(prefix="skullbase-integrated-"))
            ct_path = self.directory / "ct.nii.gz"
            slicer.util.saveNode(volume, str(ct_path))
            request = {
                "ct_path": str(ct_path),
                "target_ras_mm": target,
                "instrument": {
                    "shaft_diameter_mm": self.planner.diameter.value,
                    "portal_diameter_mm": self.planner.portalDiameter.value,
                    "length_mm": self.planner.instrumentLength.value,
                },
                "cache_directory": str(
                    Path.home() / ".skullbase-corridor" / "inference-cache"
                ),
                "inference_backend": (
                    "local" if self.planner.inferenceBackend.currentIndex == 0 else "azure"
                ),
                "local_device": self.planner.localDevice.currentText,
            }
            request_path = self.directory / "request.json"
            request_path.write_text(json.dumps(request, indent=2, allow_nan=False))
            self.output = self.directory / "result.json"
            self.process = qt.QProcess(self)
            environment = qt.QProcessEnvironment.systemEnvironment()
            for key in (
                "PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH",
                "QT_QPA_PLATFORM_PLUGIN_PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
            ):
                environment.remove(key)
            if request["inference_backend"] == "azure":
                environment.insert("AZURE_ML_ENDPOINT_URL", self.planner.endpointUrl.text)
                environment.insert("AZURE_ML_ENDPOINT_TOKEN", self.planner.endpointToken.text)
            self.process.setProcessEnvironment(environment)
            self.process.setProcessChannelMode(qt.QProcess.MergedChannels)
            self.process.connect("finished(int,QProcess::ExitStatus)", self.finished)
            self.process.connect("errorOccurred(QProcess::ProcessError)", self.failed)
            status = (
                "Running locally / checking verified cache…"
                if request["inference_backend"] == "local"
                else "Uploading CT to optional Azure service / checking verified cache…"
            )
            self.planner.setPlanningBusy(True, status)
            self.process.start(str(python), [
                "-m", "skullbase_corridor.application.e2e",
                "--request", str(request_path), "--output", str(self.output),
            ])
        except (ValueError, OSError, RuntimeError) as error:
            self.planner.setPlanningBusy(False, str(error))

    def cancel(self, remove=True):
        if self.process and self.process.state() != qt.QProcess.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(3000):
                self.process.kill()
        self.planner.setPlanningBusy(False, "Planning cancelled.")
        if remove and self.directory:
            shutil.rmtree(self.directory, ignore_errors=True)
            self.directory = None

    def failed(self, *_):
        self.planner.setPlanningBusy(False, "Planning process failed to start.")

    def finished(self, code, *_):
        raw = self.process.readAllStandardOutput().data()
        log = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if code:
            self.planner.setPlanningBusy(False, "Planning failed: " + log[-800:])
            return
        try:
            self.result = json.loads(self.output.read_text())
            self.importAnatomy()
            self.planner.setCandidates(
                self.result["proposal"]["candidates"], self.result["exact_paths"])
            self.planner.state.SetParameter("IntegratedResult", json.dumps(self.result))
            self.planner.state.SetParameter(
                "ProposalTargetRAS",
                json.dumps(self.result["proposal"]["target_ras_mm"], allow_nan=False),
            )
            if self.planner.candidateList.currentItem():
                self.planner.state.SetParameter(
                    "SelectedCandidateID",
                    self.planner.candidateList.currentItem().data(qt.Qt.UserRole),
                )
            self.planner.setPlanningBusy(
                False,
                f"{len(self.result['exact_paths'])} exact path(s) ready. "
                "Predicted anatomy requires review.",
            )
        except (OSError, ValueError, KeyError) as error:
            self.planner.setPlanningBusy(False, f"Could not import planning result: {error}")

    def importAnatomy(self):
        segmentation = self.planner.state.GetNodeReference("IntegratedAnatomy")
        if segmentation is None:
            segmentation = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode", "Predicted corridor anatomy")
            segmentation.CreateDefaultDisplayNodes()
            self.planner.state.SetNodeReferenceID("IntegratedAnatomy", segmentation.GetID())
        segmentation.GetSegmentation().RemoveAllSegments()
        volume = self.planner.volume.currentNode()
        segmentation.SetReferenceImageGeometryParameterFromVolumeNode(volume)
        for name, path in self.result["anatomy"]["mask_files"].items():
            array = np.load(path, allow_pickle=False).transpose(2, 1, 0)
            temporary = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLabelMapVolumeNode", f"{name} predicted")
            slicer.util.updateVolumeFromArray(temporary, array.astype(np.uint8))
            temporary.SetIJKToRASMatrix(self._ijkToRas(volume))
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                temporary, segmentation)
            slicer.mrmlScene.RemoveNode(temporary)
        segmentation.SetAttribute("Skullbase.AnatomyStatus", "predicted")
        segmentation.SetAttribute(
            "Skullbase.Provenance",
            json.dumps(self.result["inference"]["provenance"], allow_nan=False),
        )
        display = segmentation.GetDisplayNode()
        display.SetVisibility2D(True)
        display.SetVisibility3D(True)
        display.SetOpacity3D(0.25)

    @staticmethod
    def _ijkToRas(volume):
        matrix = vtk.vtkMatrix4x4()
        volume.GetIJKToRASMatrix(matrix)
        return matrix
