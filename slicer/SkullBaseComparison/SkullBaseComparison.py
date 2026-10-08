"""Landmark-based EEA/CTM comparison inside 3D Slicer.

This module measures user-defined anatomy; it does not infer operative access.
Install as an additional scripted module path from this checkout.
"""
import importlib.util
import json
import os
from pathlib import Path

import numpy as np
import ctk
import qt
import vtk
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleWidget,
)
from slicer.util import VTKObservationMixin

import slicer


def measurement_function():
    # Load only the numpy-based core, without installing the standalone app or
    # changing Slicer's scientific Python dependencies.
    path = Path(__file__).resolve().parents[2] / "src/corridorkit/analysis/anatomical.py"
    spec = importlib.util.spec_from_file_location("corridor_anatomical_measurements", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compare_landmarks


class SkullBaseComparison(ScriptedLoadableModule):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent.title = "CorridorKit"
        self.parent.categories = ["IGT"]
        self.parent.contributors = ["CorridorKit contributors"]
        self.parent.helpText = (
            "Select an anatomical CT and place EEA entry, contralateral maxillary entry, "
            "a common target, and a two-point petrous ICA reference axis. "
            "Angles are acute axial projections, not safety or feasibility estimates."
        )
        self.parent.acknowledgementText = "Research software; see repository methods and references."


class SkullBaseComparisonWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    PROPOSAL_ANATOMY_ROLES = (
        "left_nasal_cavity", "right_nasal_cavity",
        "left_maxillary_sinus", "right_maxillary_sinus", "bone",
        "hard_palate", "upper_teeth", "left_orbit", "right_orbit", "protected",
    )
    ROLES = (
        ("Target", "Common skull-base target (required)", "vtkMRMLMarkupsFiducialNode", 1),
        ("EEAEntry", "EEA entry", "vtkMRMLMarkupsFiducialNode", 1),
        ("CTMEntry", "Contralateral maxillary entry", "vtkMRMLMarkupsFiducialNode", 1),
        ("ICAAxis", "Petrous ICA reference axis (optional)", "vtkMRMLMarkupsLineNode", 2),
        ("EEALimit", "Optional EEA lateral limit", "vtkMRMLMarkupsFiducialNode", 1),
        ("CTMLimit", "Optional CTM lateral limit", "vtkMRMLMarkupsFiducialNode", 1),
        ("LateralAxis", "Optional medial → lateral axis", "vtkMRMLMarkupsLineNode", 2),
    )

    def __init__(self, parent=None):
        self.selectors = {}
        self.models = {}
        self.report = None
        self.state = None
        self.restoring = False
        self.landmarkObservers = []
        self.integratedController = None
        self.integratedCandidates = []
        self.integratedExact = {}
        VTKObservationMixin.__init__(self)
        # Slicer calls setup immediately when no parent was supplied.
        ScriptedLoadableModuleWidget.__init__(self, parent)

    def setup(self):
        super().setup()
        self.compare = measurement_function()
        note = qt.QLabel(
            "ANATOMICAL MEASUREMENTS ONLY\n"
            "No collision, bone-removal, visibility or surgical-safety assessment.\n"
            "Place the target first. Entry suggestions are geometric proposals requiring review."
        )
        note.wordWrap = True
        self.layout.addWidget(note)
        form = qt.QFormLayout()
        self.layout.addLayout(form)
        self.volume = slicer.qMRMLNodeComboBox()
        self.volume.nodeTypes = ["vtkMRMLScalarVolumeNode"]
        self.volume.noneEnabled = True
        self.volume.addEnabled = False
        self.volume.removeEnabled = False
        self.volume.setMRMLScene(slicer.mrmlScene)
        form.addRow("Anatomical CT", self.volume)
        self.volume.connect("currentNodeChanged(vtkMRMLNode*)", self.inputsChanged)
        for role, title, node_type, _ in self.ROLES:
            row = qt.QWidget()
            row_layout = qt.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            selector = slicer.qMRMLNodeComboBox()
            selector.nodeTypes = [node_type]
            selector.baseName = title
            selector.noneEnabled = True
            selector.addEnabled = True
            selector.removeEnabled = False
            selector.setMRMLScene(slicer.mrmlScene)
            self.selectors[role] = selector
            row_layout.addWidget(selector)
            place = slicer.qSlicerMarkupsPlaceWidget()
            place.setMRMLScene(slicer.mrmlScene)
            place.buttonsVisible = False
            place.placeButton().show()
            selector.connect("currentNodeChanged(vtkMRMLNode*)", place.setCurrentNode)
            selector.connect("currentNodeChanged(vtkMRMLNode*)", self.inputsChanged)
            row_layout.addWidget(place)
            form.addRow(title, row)
        self.diameter = qt.QDoubleSpinBox()
        self.diameter.setRange(0.1, 20)
        self.diameter.setSuffix(" mm")
        self.diameter.setValue(2)
        self.diameter.connect("valueChanged(double)", self.inputsChanged)
        form.addRow("Instrument shaft diameter", self.diameter)
        self.portalDiameter = qt.QDoubleSpinBox()
        self.portalDiameter.setRange(0.5, 30)
        self.portalDiameter.setSuffix(" mm")
        self.portalDiameter.setValue(8)
        form.addRow("Portal diameter", self.portalDiameter)
        self.instrumentLength = qt.QDoubleSpinBox()
        self.instrumentLength.setRange(10, 500)
        self.instrumentLength.setSuffix(" mm")
        self.instrumentLength.setValue(200)
        form.addRow("Instrument length", self.instrumentLength)
        fit = qt.QPushButton("Show full CT in linked views")
        fit.connect("clicked()", self.showVolume)
        self.layout.addWidget(fit)
        self.planButton = qt.QPushButton("Plan Corridors")
        self.planButton.toolTip = (
            "Infer anatomy on Azure CPU, propose entries, evaluate exact paths, and render results")
        self.planButton.connect("clicked()", self.planCorridors)
        self.layout.addWidget(self.planButton)
        self.cancelPlanButton = qt.QPushButton("Cancel planning")
        self.cancelPlanButton.enabled = False
        self.cancelPlanButton.connect("clicked()", self.cancelPlanning)
        self.layout.addWidget(self.cancelPlanButton)
        self.planningProgress = qt.QProgressBar()
        self.planningProgress.setRange(0, 1)
        self.planningProgress.value = 0
        self.layout.addWidget(self.planningProgress)
        self.candidateList = qt.QListWidget()
        self.candidateList.setMaximumHeight(150)
        self.candidateList.connect("itemSelectionChanged()", self.integratedCandidateSelected)
        self.layout.addWidget(self.candidateList)
        advanced = ctk.ctkCollapsibleButton()
        advanced.text = "Advanced / manual review"
        advanced.collapsed = True
        advancedLayout = qt.QVBoxLayout(advanced)
        cloudForm = qt.QFormLayout()
        self.inferenceBackend = qt.QComboBox()
        self.inferenceBackend.addItems(["Local workstation", "Optional Azure service"])
        self.inferenceBackend.currentIndex = int(
            slicer.app.settings().value("SkullBaseComparison/InferenceBackend", 0))
        cloudForm.addRow("Anatomy inference", self.inferenceBackend)
        self.localDevice = qt.QComboBox()
        self.localDevice.addItems(["cpu", "gpu", "mps"])
        self.localDevice.currentText = slicer.app.settings().value(
            "SkullBaseComparison/LocalDevice", "cpu")
        cloudForm.addRow("Local device", self.localDevice)
        self.endpointUrl = qt.QLineEdit()
        self.endpointUrl.placeholderText = "https://…"
        self.endpointUrl.text = slicer.app.settings().value(
            "SkullBaseComparison/EndpointUrl", "")
        cloudForm.addRow("Azure endpoint", self.endpointUrl)
        self.endpointToken = qt.QLineEdit()
        self.endpointToken.echoMode = qt.QLineEdit.Password
        self.endpointToken.text = slicer.app.settings().value(
            "SkullBaseComparison/EndpointToken", "")
        cloudForm.addRow("Endpoint token", self.endpointToken)
        self.runtimePath = qt.QLineEdit(
            slicer.app.settings().value("SkullBaseComparison/RuntimePath", ""))
        cloudForm.addRow("Managed runtime", self.runtimePath)
        saveCloud = qt.QPushButton("Save product configuration")
        saveCloud.connect("clicked()", self.saveProductConfiguration)
        cloudForm.addRow(saveCloud)
        advancedLayout.addLayout(cloudForm)
        suggest = qt.QPushButton("Use reviewed segments for entry proposals…")
        suggest.connect("clicked()", self.openEntryProposals)
        advancedLayout.addWidget(suggest)
        self.summary = qt.QLabel(
            "Select a CT and place the target. ICA is optional; entries may be placed or proposed.")
        self.summary.wordWrap = True
        self.layout.addWidget(self.summary)
        self.exportButton = qt.QPushButton("Export measurements…")
        self.exportButton.enabled = False
        self.exportButton.connect("clicked()", self.export)
        advancedLayout.addWidget(self.exportButton)
        analysis = qt.QPushButton("Compare segmented target access…")
        analysis.connect("clicked()", self.openCorridorAnalysis)
        advancedLayout.addWidget(analysis)
        self.layout.addWidget(advanced)
        self.layout.addWidget(qt.QLabel("Save the Slicer scene (.mrb) to retain images and landmarks."))
        self.layout.addStretch()
        self.addObserver(slicer.mrmlScene, slicer.vtkMRMLScene.EndCloseEvent, self.sceneClosed)
        self.addObserver(slicer.mrmlScene, slicer.vtkMRMLScene.EndImportEvent, self.restoreState)
        self.restoreState()

    def restoreState(self, *_):
        self.state = None
        for node in slicer.util.getNodesByClass("vtkMRMLScriptedModuleNode"):
            if node.GetModuleName() == "SkullBaseComparison":
                self.state = node
                break
        if self.state is None:
            self.state = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScriptedModuleNode")
            self.state.SetModuleName("SkullBaseComparison")
        self.restoring = True
        self.volume.setCurrentNode(self.state.GetNodeReference("CT"))
        for role, selector in self.selectors.items():
            selector.setCurrentNode(self.state.GetNodeReference(role))
        self.diameter.setValue(float(self.state.GetParameter("Diameter") or 2))
        self.portalDiameter.setValue(float(self.state.GetParameter("PortalDiameter") or 8))
        self.instrumentLength.setValue(float(self.state.GetParameter("InstrumentLength") or 200))
        integrated = self.state.GetParameter("IntegratedResult")
        if integrated:
            try:
                result = json.loads(integrated)
                self.setCandidates(
                    result["proposal"]["candidates"], result["exact_paths"])
                selected = self.state.GetParameter("SelectedCandidateID")
                for index in range(self.candidateList.count):
                    item = self.candidateList.item(index)
                    if item.data(qt.Qt.UserRole) == selected:
                        self.candidateList.setCurrentItem(item)
                        break
            except (TypeError, ValueError, KeyError):
                self.state.SetParameter("IntegratedResult", "")
        self.restoring = False
        self.inputsChanged()

    def sceneClosed(self, *_):
        self.models = {}
        self.restoreState()

    def inputsChanged(self, *_):
        if self.restoring or self.state is None:
            return
        # Scene observers remain; replace only landmark/transform observers.
        for node, tag in self.landmarkObservers:
            node.RemoveObserver(tag)
        self.landmarkObservers = []
        self.state.SetNodeReferenceID("CT", self.volume.currentNodeID)
        volume = self.volume.currentNode()
        if volume:
            for event in (vtk.vtkCommand.ModifiedEvent,
                          slicer.vtkMRMLTransformableNode.TransformModifiedEvent):
                self.landmarkObservers.append(
                    (volume, volume.AddObserver(event, self.updateMeasurements)))
        for role, selector in self.selectors.items():
            node = selector.currentNode()
            self.state.SetNodeReferenceID(role, node.GetID() if node else None)
            if node:
                for event in (
                    slicer.vtkMRMLMarkupsNode.PointModifiedEvent,
                    slicer.vtkMRMLMarkupsNode.PointAddedEvent,
                    slicer.vtkMRMLMarkupsNode.PointRemovedEvent,
                    slicer.vtkMRMLMarkupsNode.PointPositionDefinedEvent,
                    slicer.vtkMRMLMarkupsNode.PointPositionUndefinedEvent,
                    vtk.vtkCommand.ModifiedEvent,
                    slicer.vtkMRMLTransformableNode.TransformModifiedEvent,
                ):
                    self.landmarkObservers.append(
                        (node, node.AddObserver(event, self.updateMeasurements)))
        proposal_nodes = {
            self.state.GetNodeReference("Proposal_" + role)
            for role in self.PROPOSAL_ANATOMY_ROLES
        }
        for node in proposal_nodes - {None}:
            for event in (
                vtk.vtkCommand.ModifiedEvent,
                slicer.vtkMRMLTransformableNode.TransformModifiedEvent,
            ):
                self.landmarkObservers.append(
                    (node, node.AddObserver(event, self.proposalAnatomyChanged)))
        self.state.SetParameter("Diameter", str(self.diameter.value))
        self.state.SetParameter("PortalDiameter", str(self.portalDiameter.value))
        self.state.SetParameter("InstrumentLength", str(self.instrumentLength.value))
        self.updateMeasurements()

    def externalPython(self):
        configured = self.runtimePath.text if hasattr(self, "runtimePath") else ""
        configured = configured or os.environ.get("CORRIDORKIT_PYTHON", "")
        if configured:
            return Path(configured)
        bundled = Path(__file__).resolve().parents[1] / "runtime/bin/python"
        return bundled

    def saveProductConfiguration(self):
        endpoint = self.endpointUrl.text.strip()
        token = self.endpointToken.text
        runtime = self.runtimePath.text.strip()
        if self.inferenceBackend.currentIndex == 1 and not endpoint.startswith("https://"):
            self.summary.text = "Azure endpoint must use HTTPS."
            return
        settings = slicer.app.settings()
        settings.setValue(
            "SkullBaseComparison/InferenceBackend", self.inferenceBackend.currentIndex)
        settings.setValue("SkullBaseComparison/LocalDevice", self.localDevice.currentText)
        settings.setValue("SkullBaseComparison/EndpointUrl", endpoint)
        settings.setValue("SkullBaseComparison/EndpointToken", token)
        settings.setValue("SkullBaseComparison/RuntimePath", runtime)
        self.summary.text = (
            "Configuration saved outside the scene. Local inference keeps CT data on "
            "this workstation. Do not share settings containing an optional endpoint token.")

    def planningController(self):
        if self.integratedController is None:
            path = Path(__file__).with_name("IntegratedPlanning.py")
            spec = importlib.util.spec_from_file_location("corridor_integrated_planning", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.integratedController = module.IntegratedPlanningController(self)
        return self.integratedController

    def planCorridors(self):
        self.showVolume()
        self.planningController().start()

    def cancelPlanning(self):
        if self.integratedController:
            self.integratedController.cancel()

    def setPlanningBusy(self, busy, message):
        self.planButton.enabled = not busy
        self.cancelPlanButton.enabled = busy
        self.planningProgress.setRange(0, 0 if busy else 1)
        self.planningProgress.value = 0 if busy else 1
        self.summary.text = message

    def setCandidates(self, candidates, exact_paths):
        self.integratedCandidates = list(candidates)
        self.integratedExact = {item["candidate_id"]: item for item in exact_paths}
        self.candidateList.clear()
        for candidate in self.integratedCandidates:
            exact = self.integratedExact.get(candidate["candidate_id"], {})
            state = exact.get("state", "invalid")
            distance = exact.get("depth_mm")
            suffix = f" · {distance:.1f} mm" if distance is not None else ""
            item = qt.QListWidgetItem(
                f"{candidate['approach'].upper()} {candidate['side']} · {state}{suffix}")
            item.setData(qt.Qt.UserRole, candidate["candidate_id"])
            item.setToolTip(
                exact.get("reason") or
                "Feasible only under represented model constraints; not a safety finding.")
            self.candidateList.addItem(item)
        if self.candidateList.count:
            self.candidateList.setCurrentRow(0)

    def integratedCandidateSelected(self):
        selected = self.candidateList.selectedItems()
        if len(selected) != 1:
            return
        candidate_id = selected[0].data(qt.Qt.UserRole)
        self.state.SetParameter("SelectedCandidateID", candidate_id)
        candidate = next(
            item for item in self.integratedCandidates
            if item["candidate_id"] == candidate_id)
        exact = self.integratedExact[candidate_id]
        candidate = dict(candidate)
        candidate["exact_state"] = exact["state"]
        candidate["exact_reason"] = exact.get("reason")
        self.applyEntryProposal(candidate)
        if candidate.get("internal_medial_wall_ras_mm"):
            self.drawMedialWall(candidate)
        self.summary.text = (
            f"{candidate_id}: {exact['state']}\n"
            f"{exact.get('reason') or 'No represented collision found.'}\n"
            "This is a model result, not a safety determination.")

    def points(self, role, count):
        node = self.selectors[role].currentNode()
        if node is None or node.GetNumberOfDefinedControlPoints() != count:
            raise ValueError(f"{role}: place exactly {count} defined point(s)")
        if node.GetNumberOfControlPoints() != count:
            raise ValueError(f"{role}: remove extra or undefined control points")
        result = []
        volume = self.volume.currentNode()
        ras_to_ijk = vtk.vtkMatrix4x4()
        volume.GetRASToIJKMatrix(ras_to_ijk)
        world_to_volume = vtk.vtkGeneralTransform()
        slicer.vtkMRMLTransformNode.GetTransformBetweenNodes(
            None, volume.GetParentTransformNode(), world_to_volume)
        dimensions = volume.GetImageData().GetDimensions()
        for index in range(count):
            point = [0., 0., 0.]
            node.GetNthControlPointPositionWorld(index, point)
            local = world_to_volume.TransformPoint(point)
            ijk = ras_to_ijk.MultiplyPoint((*local, 1))
            if any(ijk[i] < -0.5 or ijk[i] > dimensions[i] - 0.5 for i in range(3)):
                raise ValueError(f"{role}: landmark is outside the selected CT field of view")
            result.append(point)
        return result

    def updateMeasurements(self, *_):
        self.report = None
        self.exportButton.enabled = False
        self.invalidateProposalIfTargetChanged()
        if hasattr(self, "entryProposalDialog"):
            self.entryProposalDialog.targetMayHaveChanged()
        try:
            if self.volume.currentNode() is None:
                raise ValueError("Select the anatomical CT first.")
            if self.volume.currentNode().GetImageData() is None:
                raise ValueError("Selected CT has no image data.")
            target = self.points("Target", 1)[0]
            entries = {}
            for name in ("EEA", "CTM"):
                selector = self.selectors[name + "Entry"]
                node = selector.currentNode()
                if node and node.GetAttribute("SkullbaseProposal.State") != "unavailable":
                    entries[name] = self.points(name + "Entry", 1)[0]
            if not entries:
                raise ValueError(
                    "Target is valid. Place entries manually or choose Suggest entries…")
            ica = None
            if self.selectors["ICAAxis"].currentNode():
                ica = self.points("ICAAxis", 2)
            optional = {}
            if any(self.selectors[role].currentNode() for role in
                   ("EEALimit", "CTMLimit", "LateralAxis")):
                if not all(name in entries for name in ("EEA", "CTM")):
                    raise ValueError("Both entries are required for lateral-limit comparison.")
                axis = self.points("LateralAxis", 2)
                optional = {
                    "eea_limit": self.points("EEALimit", 1)[0],
                    "ctm_limit": self.points("CTMLimit", 1)[0],
                    "lateral_axis": (np.array(axis[1]) - axis[0]).tolist(),
                }
            if all(name in entries for name in ("EEA", "CTM")) and ica is not None:
                report = self.compare(
                    entries["EEA"], entries["CTM"], target, *ica, **optional)
            else:
                report = {
                    "eea_angle_deg": None,
                    "ctm_angle_deg": None,
                    "angle_advantage_deg": None,
                    "eea_working_distance_mm": (
                        float(np.linalg.norm(np.asarray(entries["EEA"]) - target))
                        if "EEA" in entries else None),
                    "ctm_working_distance_mm": (
                        float(np.linalg.norm(np.asarray(entries["CTM"]) - target))
                        if "CTM" in entries else None),
                    "additional_lateral_reach_mm": None,
                }
            report["illustrated_shaft_diameter_mm"] = self.diameter.value
            report["source_volume_name"] = self.volume.currentNode().GetName()
            report["ica_axis_supplied"] = ica is not None
            self.report = report
            lines = ["Straight-line working distance"]
            for name in ("EEA", "CTM"):
                distance = report[name.lower() + "_working_distance_mm"]
                lines.append(f"{name}  {distance:.1f} mm" if distance is not None
                             else f"{name}  not defined")
            if ica is None:
                lines.extend(["", "ICA axis not supplied; axial angles unavailable."])
            elif all(name in entries for name in ("EEA", "CTM")):
                lines.extend([
                    "", "Axial angle to petrous ICA",
                    (
                        f"EEA  {report['eea_angle_deg']:.1f}°     "
                        f"CTM  {report['ctm_angle_deg']:.1f}°"
                    ),
                    f"Difference (EEA − CTM)  {report['angle_advantage_deg']:+.1f}°",
                ])
            lines.extend(["", "Proposed/illustrated shafts only. Access and clearance NOT ASSESSED."])
            self.summary.setText("\n".join(lines))
            for name, entry in entries.items():
                color = (0.23, 0.65, 1.0) if name == "EEA" else (0.94, 0.60, 0.24)
                self.drawShaft(name, entry, target, color)
            for name in {"EEA", "CTM"} - set(entries):
                model = self.state.GetNodeReference("Shaft" + name)
                if model and model.GetDisplayNode():
                    model.GetDisplayNode().SetVisibility(False)
            self.exportButton.enabled = True
        except ValueError as error:
            self.summary.setText(str(error))
            for name in ("EEA", "CTM"):
                model = self.state.GetNodeReference("Shaft" + name)
                if model and model.GetDisplayNode():
                    model.GetDisplayNode().SetVisibility(False)

    def drawShaft(self, name, entry, tip, color):
        line = vtk.vtkLineSource()
        line.SetPoint1(*entry)
        line.SetPoint2(*tip)
        tube = vtk.vtkTubeFilter()
        tube.SetInputConnection(line.GetOutputPort())
        tube.SetRadius(self.diameter.value / 2)
        tube.SetNumberOfSides(32)
        tube.CappingOn()
        tube.Update()
        model = self.state.GetNodeReference("Shaft" + name)
        if model is None:
            model = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", name + " intended shaft")
            model.CreateDefaultDisplayNodes()
            self.state.SetNodeReferenceID("Shaft" + name, model.GetID())
        model.SetAndObservePolyData(tube.GetOutput())
        display = model.GetDisplayNode()
        display.SetColor(*color)
        display.SetOpacity(0.65)
        display.SetVisibility2D(True)
        display.SetVisibility(True)
        self.models[name] = model

    def applyEntryProposal(self, candidate):
        """Place the exact returned point and visualize its shaft and portal."""
        name = candidate["approach"].upper()
        role = name + "Entry"
        self.state.SetParameter(
            "ProposalTargetRAS", json.dumps(candidate["target_ras_mm"], allow_nan=False))
        node = self.selectors[role].currentNode()
        if node is None:
            node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", name + " proposed entry")
            self.selectors[role].setCurrentNode(node)
        node.RemoveAllControlPoints()
        point = candidate["entry_ras_mm"]
        node.AddControlPoint(vtk.vtkVector3d(*point))
        node.SetAttribute("SkullbaseProposal.CandidateID", candidate["candidate_id"])
        state = candidate.get(
            "exact_state",
            "conditional" if candidate.get("conditional_constraints") else "proposed",
        )
        node.SetAttribute("SkullbaseProposal.State", state)
        node.SetAttribute("SkullbaseProposal.Reason", candidate.get("exact_reason") or "")
        target = self.points("Target", 1)[0]
        stateColors = {
            "blocked": (0.95, 0.20, 0.20),
            "unavailable": (0.65, 0.65, 0.65),
            "conditional": (1.0, 0.75, 0.25),
            "invalid": (0.8, 0.2, 0.8),
            "model_feasible": (0.20, 0.80, 0.45),
        }
        self.drawShaft(
            name, point, target,
            stateColors.get(
                state, (0.23, 0.65, 1.0) if name == "EEA" else (0.94, 0.60, 0.24)))
        self.state.GetNodeReference("Shaft" + name).SetAttribute(
            "SkullbaseProposal.State", state)
        self.drawPortal(name, point, target, state)
        self.styleTarget()
        self.updateMeasurements()

    def styleTarget(self):
        target = self.selectors["Target"].currentNode()
        if target is None:
            return
        target.CreateDefaultDisplayNodes()
        display = target.GetDisplayNode()
        display.SetSelectedColor(1.0, 0.25, 0.25)
        display.SetColor(1.0, 0.25, 0.25)
        display.SetGlyphScale(2.0)
        display.SetTextScale(1.5)
        display.SetVisibility2D(True)
        display.SetVisibility3D(True)

    def drawMedialWall(self, candidate):
        point = candidate["internal_medial_wall_ras_mm"]
        role = "MedialWall_" + candidate["candidate_id"]
        node = self.state.GetNodeReference(role)
        if node is None:
            node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", "CTM medial-wall crossing")
            node.CreateDefaultDisplayNodes()
            self.state.SetNodeReferenceID(role, node.GetID())
        node.RemoveAllControlPoints()
        node.AddControlPoint(vtk.vtkVector3d(*point), "Medial wall")
        display = node.GetDisplayNode()
        display.SetColor(1.0, 0.85, 0.2)
        display.SetSelectedColor(1.0, 0.85, 0.2)
        display.SetGlyphScale(1.6)
        display.SetVisibility2D(True)
        display.SetVisibility3D(True)

    def invalidateEntryProposals(self):
        """Hide proposal-only geometry without deleting persisted scene nodes."""
        for name in ("EEA", "CTM"):
            portal = self.state.GetNodeReference("Portal" + name)
            if portal and portal.GetDisplayNode():
                portal.GetDisplayNode().SetVisibility(False)
            entry = self.selectors[name + "Entry"].currentNode()
            if (entry and entry.GetAttribute("SkullbaseProposal.CandidateID")
                    and entry.GetAttribute("SkullbaseProposal.State") != "unavailable"):
                entry.SetAttribute("SkullbaseProposal.State", "unavailable")
                shaft = self.state.GetNodeReference("Shaft" + name)
                if shaft and shaft.GetDisplayNode():
                    shaft.SetAttribute("SkullbaseProposal.State", "unavailable")
                    shaft.GetDisplayNode().SetVisibility(False)
                if portal:
                    portal.SetAttribute("SkullbaseProposal.State", "unavailable")

    def invalidateProposalIfTargetChanged(self):
        raw = self.state.GetParameter("ProposalTargetRAS")
        if not raw:
            return
        try:
            expected = np.asarray(json.loads(raw), dtype=float)
            node = self.selectors["Target"].currentNode()
            if node is None or node.GetNumberOfDefinedControlPoints() != 1:
                changed = True
            else:
                point = [0.0, 0.0, 0.0]
                node.GetNthControlPointPositionWorld(0, point)
                changed = not np.allclose(point, expected, atol=1e-6, rtol=0)
        except (TypeError, ValueError):
            changed = True
        if changed:
            self.state.SetParameter("ProposalTargetRAS", "")
            self.invalidateEntryProposals()

    def proposalAnatomyChanged(self, *_):
        self.invalidateEntryProposals()
        self.state.SetParameter("ProposalTargetRAS", "")
        if hasattr(self, "entryProposalDialog"):
            self.entryProposalDialog.invalidate(
                "Anatomy changed; previous proposals are stale.")

    def drawPortal(self, name, entry, target, state):
        """Draw a distinct thin portal/window disc normal to the shaft."""
        direction = np.asarray(target, dtype=float) - np.asarray(entry, dtype=float)
        length = float(np.linalg.norm(direction))
        if length <= 1e-8:
            return
        direction /= length
        source = vtk.vtkCylinderSource()
        source.SetRadius(max(2.0, self.diameter.value))
        source.SetHeight(0.8)
        source.SetResolution(48)
        source.CappingOn()
        transform = vtk.vtkTransform()
        transform.Translate(*entry)
        transform.RotateWXYZ(
            np.degrees(np.arccos(np.clip(direction[1], -1.0, 1.0))),
            direction[2], 0.0, -direction[0])
        transformed = vtk.vtkTransformPolyDataFilter()
        transformed.SetTransform(transform)
        transformed.SetInputConnection(source.GetOutputPort())
        transformed.Update()
        role = "Portal" + name
        model = self.state.GetNodeReference(role)
        if model is None:
            label = "nasal portal" if name == "EEA" else "maxillary window"
            model = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLModelNode", name + " proposed " + label)
            model.CreateDefaultDisplayNodes()
            self.state.SetNodeReferenceID(role, model.GetID())
        model.SetAndObservePolyData(transformed.GetOutput())
        model.SetAttribute("SkullbaseProposal.State", state)
        display = model.GetDisplayNode()
        stateColors = {
            "blocked": (0.95, 0.20, 0.20),
            "unavailable": (0.65, 0.65, 0.65),
            "conditional": (1.0, 0.75, 0.25),
            "invalid": (0.8, 0.2, 0.8),
            "model_feasible": (0.20, 0.80, 0.45),
        }
        display.SetColor(*stateColors.get(
            state, (0.30, 0.85, 1.0) if name == "EEA" else (1.0, 0.75, 0.25)))
        display.SetOpacity(0.8)
        display.SetVisibility2D(True)
        display.SetVisibility(True)

    def showVolume(self):
        node = self.volume.currentNode()
        if node:
            slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
            slicer.util.setSliceViewerLayers(background=node)
            slicer.util.resetSliceViews()
            slicer.util.resetThreeDViews()

    def export(self):
        self.updateMeasurements()
        if self.report is None:
            return
        filename = qt.QFileDialog.getSaveFileName(
            slicer.util.mainWindow(), "Export anatomical measurements", "", "JSON (*.json)")
        if filename:
            with open(filename, "w") as stream:
                json.dump(self.report, stream, indent=2, allow_nan=False)

    def openCorridorAnalysis(self):
        if not hasattr(self, "corridorDialog"):
            path = Path(__file__).with_name("CorridorAnalysis.py")
            spec = importlib.util.spec_from_file_location("corridor_slicer_dialog", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.corridorDialog = module.CorridorAnalysisDialog(self)
        self.corridorDialog.show()
        self.corridorDialog.raise_()

    def openEntryProposals(self):
        if not hasattr(self, "entryProposalDialog"):
            path = Path(__file__).with_name("EntryProposals.py")
            spec = importlib.util.spec_from_file_location("corridor_entry_proposals", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.entryProposalDialog = module.EntryProposalDialog(self)
        self.entryProposalDialog.show()
        self.entryProposalDialog.raise_()

    def cleanup(self):
        if self.integratedController:
            self.integratedController.cancel()
        if hasattr(self, "entryProposalDialog"):
            self.entryProposalDialog.cleanup()
            self.entryProposalDialog.deleteLater()
        if hasattr(self, "corridorDialog"):
            self.corridorDialog.cancelAnalysis()
            self.corridorDialog.deleteLater()
        for node, tag in self.landmarkObservers:
            node.RemoveObserver(tag)
        self.landmarkObservers = []
        self.removeObservers()
