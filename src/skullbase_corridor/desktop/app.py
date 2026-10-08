"""AutoHijdra/Spine-HU-style desktop reviewer for corridor geometry."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from skullbase_corridor.application.session import ReviewDocument
from skullbase_corridor.desktop.views import LinkedViewer
from skullbase_corridor.domain.models import CorridorCase
from skullbase_corridor.export.json import (
    export_csv,
    export_result,
    input_manifest,
    read_case,
)
from skullbase_corridor.geometry.engine import analyze_case
from skullbase_corridor.synthetic.cases import analytical_case


class AnalysisWorker(QtCore.QObject):
    completed = QtCore.Signal(object, int)
    failed = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(
        self, case: CorridorCase, base_directory: Path | None, generation: int
    ) -> None:
        super().__init__()
        self.case = case
        self.base_directory = base_directory
        self.generation = generation
        self.cancelled = False
        self.cancel_event = threading.Event()
        self.inputs = None

    @QtCore.Slot()
    def run(self) -> None:
        try:
            if not self.cancelled:
                self.inputs = input_manifest(self.case, self.base_directory)
                result = analyze_case(
                    self.case,
                    simultaneous_minimum_angle_deg=20.0,
                    base_directory=self.base_directory,
                    cancel_check=self.cancel_event.is_set,
                )
                if input_manifest(self.case, self.base_directory) != self.inputs:
                    raise ValueError("An input file changed during analysis; discard this result")
                if not self.cancelled:
                    self.completed.emit(result, self.generation)
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    @QtCore.Slot()
    def cancel(self) -> None:
        self.cancelled = True
        self.cancel_event.set()


class ReplaceCaseCommand(QtGui.QUndoCommand):
    def __init__(self, window: MainWindow, before: CorridorCase, after: CorridorCase, text: str):
        super().__init__(text)
        self.window = window
        self.before = before
        self.after = after

    def undo(self) -> None:
        self.window._set_case(self.before, from_history=True)

    def redo(self) -> None:
        self.window._set_case(self.after, from_history=True)


class MaskStrokeCommand(QtGui.QUndoCommand):
    def __init__(self, window, point, erase):
        super().__init__("Erase mask" if erase else "Paint mask")
        self.window = window
        self.editor = window.mask_editor
        self.point = point
        self.erase = erase
        self.radius = window.brush_radius.value()
        self.label = window.brush_label.value()
        self.first = True

    def redo(self) -> None:
        self.window.mask_editor = self.editor
        if self.first:
            changed = self.editor.paint(self.point, self.radius, self.label, erase=self.erase)
            self.first = False
            if not changed:
                self.setObsolete(True)
                return
        else:
            self.editor.redo()
        self.window._mask_changed()

    def undo(self) -> None:
        self.window.mask_editor = self.editor
        self.editor.undo()
        self.window._mask_changed()


class LandingPage(QtWidgets.QWidget):
    open_requested = QtCore.Signal()
    synthetic_requested = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addStretch(2)
        title = QtWidgets.QLabel("Skull-Base Corridor")
        title.setObjectName("landingTitle")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        subtitle = QtWidgets.QLabel(
            "Patient-space geometric comparison of endonasal and transmaxillary access"
        )
        subtitle.setObjectName("muted")
        subtitle.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        buttons = QtWidgets.QHBoxLayout()
        open_button = QtWidgets.QPushButton("Open case")
        open_button.setObjectName("primaryButton")
        open_button.clicked.connect(self.open_requested)
        demo_button = QtWidgets.QPushButton("Open synthetic demonstration")
        demo_button.clicked.connect(self.synthetic_requested)
        buttons.addStretch()
        buttons.addWidget(open_button)
        buttons.addWidget(demo_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        note = QtWidgets.QLabel(
            "Research software. Results describe configured geometry and do not predict safe resection."
        )
        note.setObjectName("warningText")
        note.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(note)
        layout.addStretch(3)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, case_path: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Skull-Base Corridor")
        self.resize(1500, 900)
        self.settings = QtCore.QSettings("OpenSkullBase", "Corridor")
        self.case: CorridorCase | None = None
        self.case_path: Path | None = None
        self.document: ReviewDocument | None = None
        self._analysis_inputs = None
        self._dirty = False
        self.mask_editor = None
        self._mask_pending = False
        self._image_error = False
        self._close_after_worker = False
        self.result = None
        self.thread: QtCore.QThread | None = None
        self.worker: AnalysisWorker | None = None
        self.undo_stack = QtGui.QUndoStack(self)
        self._building_controls = False
        self._generation = 0
        self._path_entry = None
        self._build_actions()
        self._build_ui()
        self._apply_theme()
        geometry = self.settings.value("windowGeometry")
        if geometry:
            self.restoreGeometry(geometry)
        if case_path:
            self.open_case(Path(case_path))
        self._populate_recent()

    def _build_actions(self) -> None:
        toolbar = QtWidgets.QToolBar("Case")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.open_action = toolbar.addAction("Open")
        self.open_action.setShortcut(QtGui.QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.choose_case)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.open_action)
        import_menu = file_menu.addMenu("Import")
        demo_menu = file_menu.addMenu("Examples")
        self.synthetic_action = demo_menu.addAction("Synthetic")
        self.synthetic_action.triggered.connect(self.open_synthetic)
        demo_menu.addAction("Planning phantom").triggered.connect(self.open_planning_phantom)
        import_menu.addAction("CT volume").triggered.connect(self.choose_ct)
        import_menu.addAction("DICOM series").triggered.connect(self.choose_dicom)
        import_menu.addAction("Segmentation mask").triggered.connect(self.choose_mask)
        import_menu.addAction("MRI overlay").triggered.connect(self.choose_mri)
        self.save_action = toolbar.addAction("Save")
        self.save_action.setShortcut(QtGui.QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_case)
        self.export_action = file_menu.addAction("Export analysis")
        self.export_action.triggered.connect(self.choose_export)
        file_menu.addAction("Save snapshot").triggered.connect(self.choose_snapshot)
        toolbar.addSeparator()
        self.undo_action = self.undo_stack.createUndoAction(self, "Undo")
        self.undo_action.setShortcut(QtGui.QKeySequence.StandardKey.Undo)
        toolbar.addAction(self.undo_action)
        self.redo_action = self.undo_stack.createRedoAction(self, "Redo")
        self.redo_action.setShortcut(QtGui.QKeySequence.StandardKey.Redo)
        toolbar.addAction(self.redo_action)
        toolbar.addSeparator()
        self.analyze_action = toolbar.addAction("Analyze")
        self.analyze_action.setShortcut("Ctrl+Return")
        self.analyze_action.triggered.connect(self.start_analysis)
        self.cancel_action = toolbar.addAction("Cancel")
        self.cancel_action.triggered.connect(self.cancel_analysis)
        self.cancel_action.setEnabled(False)

    def _build_ui(self) -> None:
        self.stack = QtWidgets.QStackedWidget()
        self.landing = LandingPage()
        self.landing.open_requested.connect(self.choose_case)
        self.landing.synthetic_requested.connect(self.open_synthetic)
        self.stack.addWidget(self.landing)

        workspace = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        banner = QtWidgets.QLabel("Research planning  ·  Geometric model only — not a surgical recommendation")
        banner.setObjectName("researchBanner")
        banner.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(banner)
        splitter = QtWidgets.QSplitter()
        self.splitter = splitter
        self.viewer = LinkedViewer()
        self.viewer.approach_display.currentIndexChanged.connect(self._approach_display_changed)
        self.coverage_legend = QtWidgets.QLabel(
            '<span style="color:#3aa6ff">● EEA only</span> &nbsp; '
            '<span style="color:#f09a3e">● Transmaxillary only</span> &nbsp; '
            '<span style="color:#b464e6">● Both</span> &nbsp; '
            '<span style="color:#9196a0">● Not reached at this sampling</span> &nbsp; '
            '<span style="color:#ebdc9b">× Unavailable</span>'
        )
        self.coverage_legend.setWordWrap(True)
        self.coverage_strip = QtWidgets.QLabel("Coverage unavailable — run analysis.")
        self.coverage_strip.setObjectName("pendingStatus")
        self.coverage_strip.setWordWrap(True)
        self.target_inspection = QtWidgets.QLabel(
            "Click a displayed target sample to inspect the paths that reach it.")
        self.target_inspection.setWordWrap(True)
        if hasattr(self.viewer, "point_picked"):
            self.viewer.point_picked.connect(self._point_picked)
        splitter.addWidget(self.viewer)
        panel = QtWidgets.QScrollArea()
        self.review_panel = panel
        panel.setWidgetResizable(True)
        panel.setMinimumWidth(320)
        panel_body = QtWidgets.QWidget()
        self.panel_layout = QtWidgets.QVBoxLayout(panel_body)
        title = QtWidgets.QLabel("Approach configuration")
        title.setObjectName("panelTitle")
        self.panel_layout.addWidget(title)
        self.case_label = QtWidgets.QLabel("No case")
        self.case_label.setObjectName("muted")
        self.case_label.setWordWrap(True)
        self.case_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                     QtWidgets.QSizePolicy.Policy.Preferred)
        self.panel_layout.addWidget(self.case_label)
        self.pick_mode = QtWidgets.QComboBox()
        self.pick_mode.addItems(["Navigate", "Place target", "Place EEA portal", "Place transmaxillary portal",
                                 "Paint mask", "Erase mask", "Draw intended path (entry then tip)"])
        self.panel_layout.addWidget(self.pick_mode)
        brush = QtWidgets.QHBoxLayout()
        self.brush_radius = QtWidgets.QDoubleSpinBox()
        self.brush_radius.setRange(.1, 20)
        self.brush_radius.setValue(2)
        self.brush_radius.setSuffix(" mm brush")
        self.brush_label = QtWidgets.QSpinBox()
        self.brush_label.setRange(1, 65535)
        self.brush_label.setPrefix("Label ")
        brush.addWidget(self.brush_radius)
        brush.addWidget(self.brush_label)
        self.panel_layout.addLayout(brush)
        save_mask = QtWidgets.QPushButton("Save / apply edited mask")
        save_mask.clicked.connect(self.save_edited_mask)
        self.panel_layout.addWidget(save_mask)
        prepare = QtWidgets.QPushButton("Configure bone-removal mask")
        prepare.clicked.connect(self.choose_bone_removal)
        self.panel_layout.addWidget(prepare)
        self.reviewer = QtWidgets.QLineEdit()
        self.reviewer.setPlaceholderText("Reviewer identity")
        self.panel_layout.addWidget(self.reviewer)
        review_button = QtWidgets.QPushButton("Record anatomy review")
        review_button.clicked.connect(self.approve_anatomy)
        self.panel_layout.addWidget(review_button)
        self.anatomy_status = QtWidgets.QLabel("Anatomy not reviewed")
        self.anatomy_status.setWordWrap(True)
        self.panel_layout.addWidget(self.anatomy_status)
        self.approach_box = QtWidgets.QWidget()
        self.approach_layout = QtWidgets.QVBoxLayout(self.approach_box)
        self.approach_layout.setContentsMargins(0, 0, 0, 0)
        self.panel_layout.addWidget(self.approach_box)
        self.run_button = QtWidgets.QPushButton("Run geometric analysis")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.start_analysis)
        self.panel_layout.addWidget(self.run_button)
        self.result_status = QtWidgets.QLabel("Results pending")
        self.result_status.setObjectName("pendingStatus")
        self.result_status.setWordWrap(True)
        self.panel_layout.addWidget(self.result_status)
        self.results = QtWidgets.QTableWidget(0, 6)
        self.results.setHorizontalHeaderLabels(
            ("Approach", "Status", "Reached", "Solid angle", "Depth", "Clearance")
        )
        self.results.verticalHeader().hide()
        self.results.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, 6):
            self.results.horizontalHeader().setSectionResizeMode(
                column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
            )
        self.panel_layout.addWidget(self.results)
        self.witness_details = QtWidgets.QPlainTextEdit()
        self.witness_details.setReadOnly(True)
        self.witness_details.setMaximumHeight(160)
        self.results.currentCellChanged.connect(self._show_witness)
        self.panel_layout.addWidget(self.witness_details)
        self.path_controls = QtWidgets.QGroupBox("Computed paths")
        path_layout = QtWidgets.QVBoxLayout(self.path_controls)
        self.path_selector = QtWidgets.QComboBox()
        self.path_selector.setMinimumContentsLength(20)
        self.path_selector.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.path_selector.currentIndexChanged.connect(self._select_path)
        path_layout.addWidget(self.path_selector, 2)
        self.focus_path_button = QtWidgets.QPushButton("Center selected path")
        self.focus_path_button.clicked.connect(self._focus_path)
        path_layout.addWidget(self.focus_path_button)
        self.path_hint = QtWidgets.QLabel(
            "Run analysis to inspect feasible paths. Dashed lines are projections; "
            "only solid segments intersect the displayed CT slice.")
        self.path_hint.setWordWrap(True)
        path_layout.addWidget(self.path_hint)
        self.path_controls.setEnabled(False)
        from skullbase_corridor.desktop.comparison import ComparisonPanel
        self.comparison_panel = ComparisonPanel()
        self.combination_text = QtWidgets.QLabel("Combined access: unavailable")
        self.combination_text.setWordWrap(True)
        self.panel_layout.addWidget(self.combination_text)
        assumptions = QtWidgets.QLabel(
            "Assumptions\n"
            "• Static anatomy and target\n"
            "• Straight rigid instruments\n"
            "• Sampled directions at displayed resolution\n"
            "• Missing protected anatomy causes abstention\n"
            "• Protected-anatomy angular quadrature accuracy remains unresolved\n"
            "• Reported voxel clearance is a conservative lower bound"
        )
        assumptions.setWordWrap(True)
        assumptions.setObjectName("assumptionBox")
        self.panel_layout.addWidget(assumptions)
        self.panel_layout.addStretch()
        self.side_tabs = QtWidgets.QTabWidget()
        configuration_scroll = QtWidgets.QScrollArea()
        configuration_scroll.setWidgetResizable(True)
        configuration_scroll.setWidget(panel_body)
        self.side_tabs.addTab(configuration_scroll, "Configuration / anatomy")
        result_page = QtWidgets.QWidget()
        result_layout = QtWidgets.QVBoxLayout(result_page)
        for widget in (self.run_button, self.result_status, self.comparison_panel,
                       self.results,
                       self.witness_details, self.combination_text, assumptions):
            self.panel_layout.removeWidget(widget)
            result_layout.addWidget(widget)
        result_layout.addStretch()
        result_scroll = QtWidgets.QScrollArea()
        result_scroll.setWidgetResizable(True)
        result_scroll.setWidget(result_page)
        self.side_tabs.addTab(result_scroll, "Planning / comparison")
        panel.setWidget(self.side_tabs)
        # The inspector owns text and workflow controls. The scan owns geometry.
        inspector = QtWidgets.QFrame()
        inspector.setObjectName("planningInspector")
        inspector.setMinimumWidth(280)
        inspector.setMaximumWidth(340)
        inspector_layout = QtWidgets.QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(14, 14, 14, 14)
        inspector_layout.setSpacing(12)
        title = QtWidgets.QLabel("Plan")
        title.setObjectName("panelTitle")
        inspector_layout.addWidget(title)
        self.plan_context = QtWidgets.QLabel("User-defined geometry")
        self.plan_context.setWordWrap(True)
        inspector_layout.addWidget(self.plan_context)
        geometry_key = QtWidgets.QLabel(
            '<span style="color:#3aa6ff">● EEA</span> &nbsp; '
            '<span style="color:#f09a3e">● CTM</span><br>'
            'Ring = entry aperture<br>Outlined region = target<br>'
            'Dashed shaft = projection<br>Solid fill = intersection with this slice')
        geometry_key.setWordWrap(True)
        inspector_layout.addWidget(geometry_key)
        planning_bar = QtWidgets.QVBoxLayout()
        self.review_toggle = QtWidgets.QPushButton("Anatomy & configuration…")
        self.review_toggle.setCheckable(True)
        self.review_toggle.toggled.connect(panel.setVisible)
        self.review_toggle.toggled.connect(
            lambda visible: None if visible else self.pick_mode.setCurrentIndex(0))
        planning_bar.addWidget(self.review_toggle)
        self.all_paths_button = QtWidgets.QPushButton("Clear target filter")
        self.all_paths_button.clicked.connect(self._clear_target_filter)
        planning_bar.addWidget(self.all_paths_button)
        self.intended_approach = QtWidgets.QComboBox()
        self.intended_approach.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.intended_approach.setMinimumContentsLength(12)
        planning_bar.addWidget(self.intended_approach)
        draw_path = QtWidgets.QPushButton("1   Draw entry → tip")
        draw_path.clicked.connect(self._begin_intended_path)
        planning_bar.addWidget(draw_path)
        assess_path = QtWidgets.QPushButton("2   Check against anatomy")
        assess_path.clicked.connect(self.check_intended_path)
        planning_bar.addWidget(assess_path)
        export_plan = QtWidgets.QPushButton("3   Export plan")
        export_plan.clicked.connect(self.export_plan)
        planning_bar.addWidget(export_plan)
        inspector_layout.addLayout(planning_bar)
        inspector_layout.addWidget(self.target_inspection)
        self.target_inspection.setObjectName("inspectorStatus")
        self.target_inspection.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                             QtWidgets.QSizePolicy.Policy.Preferred)
        inspector_layout.addWidget(QtWidgets.QLabel("Coverage"))
        inspector_layout.addWidget(self.coverage_strip)
        inspector_layout.addWidget(self.coverage_legend)
        self.coverage_strip.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                          QtWidgets.QSizePolicy.Policy.Preferred)
        inspector_layout.addWidget(self.path_controls)
        inspector_layout.addStretch()
        inspector_scroll = QtWidgets.QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setMinimumWidth(295)
        inspector_scroll.setMaximumWidth(355)
        inspector_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inspector_scroll.setWidget(inspector)
        # Keep the scan workspace wide; detailed configuration is explicitly opened.
        panel.hide()
        splitter.addWidget(panel)
        splitter.addWidget(inspector_scroll)
        splitter.setSizes([1080, 420])
        splitter.setSizes([1050, 0, 310])
        layout.addWidget(splitter, 1)
        self.stack.addWidget(workspace)
        self.setCentralWidget(self.stack)
        self.statusBar().showMessage("Open a case or synthetic demonstration")
        self._set_enabled(False)

    def _populate_recent(self) -> None:
        if not hasattr(self, "recent_list"):
            self.recent_list = QtWidgets.QListWidget()
            self.recent_list.setMaximumHeight(120)
            self.landing.layout().insertWidget(4, self.recent_list)
            self.recent_list.itemDoubleClicked.connect(
                lambda item: self.open_case(Path(item.data(QtCore.Qt.ItemDataRole.UserRole)))
            )
        self.recent_list.clear()
        for path in self.settings.value("recentCases", [], type=list):
            if Path(path).is_file():
                item = QtWidgets.QListWidgetItem(Path(path).name)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, path)
                self.recent_list.addItem(item)

    def _remember_case(self) -> None:
        paths = self.settings.value("recentCases", [], type=list)
        paths = [str(self.case_path)] + [p for p in paths if p != str(self.case_path)]
        self.settings.setValue("recentCases", paths[:10])
        self._populate_recent()

    def choose_ct(self) -> None:
        if not self._allow_replace():
            return
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import CT", "", "Medical volume (*.nii *.nii.gz *.nrrd *.mha)"
        )
        if filename:
            try:
                self.import_ct(Path(filename))
            except (OSError, ValueError, RuntimeError) as exc:
                QtWidgets.QMessageBox.critical(self, "CT import failed", str(exc))

    def import_ct(self, path: Path) -> None:
        """Create an explicitly unreviewed case; do not invent critical anatomy."""
        from skullbase_corridor.domain.models import (
            ImageReference,
            KnowledgeStatus,
            ProtectedStructure,
        )
        from skullbase_corridor.export.json import file_sha256
        from skullbase_corridor.io.volumes import load_volume

        volume = load_volume(path)
        center = (np.asarray(volume.data.shape) - 1) / 2
        physical = (volume.affine @ np.r_[center, 1])[:3]
        # The initial target is visibly a placeholder, never a detected tumor.
        data = analytical_case().model_dump(mode="json")
        data["case_id"] = "imported-research-case"
        data["coordinate_frame"] = "RAS"
        data["source_image"] = ImageReference(
            uri=str(path.resolve()), coordinate_frame="RAS", sha256=file_sha256(path)
        ).model_dump(mode="json")
        data["target"] = {"points_mm": [physical.tolist()], "source": "unreviewed_center_placeholder"}
        data["protected_structures"] = [
            ProtectedStructure(name=name, status=KnowledgeStatus.UNKNOWN).model_dump(mode="json")
            for name in ("left_carotid", "right_carotid", "other_required_critical_anatomy")
        ]
        for index, approach in enumerate(data["approaches"]):
            approach["portal"]["center_mm"] = (physical + np.array([20 * index, -40, 0])).tolist()
            approach["portal"]["normal"] = [0, 1, 0]
            approach["nominal_direction"] = [0, 1, 0]
        self.document = None
        self.case_path = None
        self.mask_editor = None
        self._mask_pending = False
        self.undo_stack.clear()
        self._set_case(CorridorCase.model_validate(data), from_history=True)
        self.anatomy_status.setText(
            "UNREVIEWED: center target and portal placeholders. Import target/protected masks "
            "and configure openings before reviewing."
        )

    def choose_dicom(self) -> None:
        if not self._allow_replace():
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select DICOM directory")
        if not folder:
            return
        import SimpleITK as sitk
        ids = sitk.ImageSeriesReader.GetGDCMSeriesIDs(folder)
        if not ids:
            QtWidgets.QMessageBox.warning(self, "DICOM", "No image series found.")
            return
        series, ok = QtWidgets.QInputDialog.getItem(self, "Select series", "Series UID", list(ids), 0, False)
        if not ok:
            return
        output, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save imported CT (local)", "", "NIfTI (*.nii.gz)"
        )
        if not output:
            return
        try:
            from skullbase_corridor.io.volumes import load_dicom_series
            volume = load_dicom_series(Path(folder), series)
            import nibabel as nib
            image = nib.Nifti1Image(volume.data, volume.affine)
            image.header.set_xyzt_units("mm")
            nib.save(image, output)
            self.import_ct(Path(output))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "DICOM import failed", str(exc))

    def choose_mask(self) -> None:
        if not self.case or not self.case.source_image:
            QtWidgets.QMessageBox.information(self, "Import mask", "Import a CT first.")
            return
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import reviewed mask", "", "Medical mask (*.nii *.nii.gz *.nrrd)"
        )
        if not filename:
            return
        role, ok = QtWidgets.QInputDialog.getItem(
            self, "Mask role", "Role", ["Target", "Protected anatomy", "Bone"], 0, False
        )
        if not ok:
            return
        try:
            from skullbase_corridor.io.masks import target_from_mask
            from skullbase_corridor.io.volumes import load_volume
            volume = load_volume(Path(filename))
            image_path = Path(self.case.source_image.uri)
            if not image_path.is_absolute():
                image_path = (self.case_path.parent if self.case_path else Path.cwd()) / image_path
            image = load_volume(image_path)
            if volume.data.shape != image.data.shape or not np.allclose(volume.affine, image.affine, atol=1e-4):
                raise ValueError("Mask and CT grids differ. Register/resample explicitly before import.")
            labels = [int(x) for x in np.unique(volume.data) if x != 0]
            if not labels or not np.allclose(volume.data, np.round(volume.data)):
                raise ValueError("A nonempty integer label mask is required")
            selected, ok = QtWidgets.QInputDialog.getItem(
                self, "Mask label", "Foreground label", list(map(str, labels)), 0, False
            )
            if not ok:
                return
            label = int(selected)
            data = self.case.model_dump(mode="json")
            if role == "Target":
                mask = volume.data == label
                stride = self._choose_target_stride(int(np.count_nonzero(mask)))
                if stride is None:
                    return
                data["target"] = target_from_mask(mask, volume.affine, stride=stride).model_dump(mode="json")
            else:
                unresolved = [s.name for s in self.case.protected_structures
                              if s.status.value != "known"]
                names = unresolved + ["Add separately named structure"]
                chosen, ok = QtWidgets.QInputDialog.getItem(
                    self, "Anatomy identity", "Mask represents", names, 0, False)
                if not ok:
                    return
                if chosen == "Add separately named structure":
                    chosen, ok = QtWidgets.QInputDialog.getText(self, "Structure name", "Name")
                    if not ok or not chosen.strip():
                        return
                structure = {
                    "name": chosen.strip(),
                    "status": "known",
                    "geometry": {
                        "kind": "voxel", "uri": str(Path(filename).resolve()),
                        "affine": volume.affine.tolist(), "foreground_values": [label],
                    },
                }
                if role == "Bone":
                    if chosen in unresolved:
                        raise ValueError("Bone cannot substitute for a required critical structure")
                    structure["tissue_type"] = "bone"
                data["protected_structures"] = [
                    s for s in data["protected_structures"] if s["name"] != chosen
                ] + [structure]
            case = CorridorCase.model_validate(data)
            self.undo_stack.push(ReplaceCaseCommand(self, self.case, case, f"Import {role} mask"))
            from skullbase_corridor.desktop.editing import MaskEditor
            self.mask_editor = MaskEditor(image, volume)
            if hasattr(self.viewer, "set_mask"):
                self.viewer.set_mask(role.lower(), volume)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Mask import failed", str(exc))

    def _point_picked(self, point) -> None:
        if not self.case:
            return
        if self.pick_mode.currentIndex() == 0:
            self._inspect_target(point)
            return
        data = self.case.model_dump(mode="json")
        selection = self.pick_mode.currentIndex()
        if selection == 6:
            if self._path_entry is None:
                self._path_entry = tuple(point)
                self.target_inspection.setText("Entry recorded. Click the intended tip; navigate slices as needed.")
            else:
                try:
                    self.add_intended_path(self.intended_approach.currentData(), self._path_entry, point)
                except ValueError as exc:
                    self.target_inspection.setText(str(exc))
                    return
                self._path_entry = None
                self.pick_mode.setCurrentIndex(0)
            return
        if selection >= 4:
            if self.mask_editor is None:
                self.statusBar().showMessage("Import a mask before using the brush.")
                return
            try:
                self.undo_stack.push(MaskStrokeCommand(self, point, selection == 5))
            except ValueError as exc:
                self.statusBar().showMessage(str(exc))
            return
        if selection == 1:
            data["target"] = {"points_mm": [list(point)], "source": "reviewer_selected_point"}
        else:
            index = min(selection - 2, len(data["approaches"]) - 1)
            data["approaches"][index]["portal"]["center_mm"] = list(point)
        updated = CorridorCase.model_validate(data)
        self.undo_stack.push(ReplaceCaseCommand(self, self.case, updated, "Place geometry"))

    def _mask_changed(self) -> None:
        self._generation += 1
        if self.worker:
            self.worker.cancel()
        self._mark_stale()
        self._dirty = True
        self._mask_pending = True
        if self.document:
            self.document.anatomy_approval_sha256 = None
            self.document.approved_inputs = None
            self.document.record("mask_brush_edit", self.reviewer.text())
        self.viewer.set_mask("edited", self.mask_editor.volume)
        self.anatomy_status.setText("Unsaved mask corrections — save/apply before analysis.")
        self.analyze_action.setEnabled(False)
        self.run_button.setEnabled(False)

    def _begin_intended_path(self):
        if self.case is None:
            return
        self._path_entry = None
        self.pick_mode.setCurrentIndex(6)
        self.target_inspection.setText(
            "Click entry, then tip on the scan. This draws an intended instrument, NOT an assessed safe path.")

    def add_intended_path(self, approach_name, entry, tip):
        from skullbase_corridor.application.session import IntendedPath
        planned = IntendedPath(approach_name=approach_name, entry_mm=tuple(entry), tip_mm=tuple(tip))
        config = next((a for a in self.case.approaches if a.name == approach_name), None)
        if config is None:
            raise ValueError("Select a configured approach")
        depth = float(np.linalg.norm(np.array(tip) - entry))
        if depth > config.instrument.length_mm:
            raise ValueError(f"Intended insertion {depth:.1f} mm exceeds instrument length")
        self.document.intended_paths = [
            p for p in self.document.intended_paths if p.approach_name != approach_name
        ] + [planned]
        self.document.record("intended_path_drawn", self.reviewer.text(),
                             "Unassessed user-defined path; no feasibility or safety assertion.")
        self._dirty = True
        self.viewer.set_intended_paths(self.document.intended_paths)
        self.viewer.inspect_intended_path(planned)
        self.target_inspection.setText(
            f"Intended insertion {depth:.1f} mm · shaft Ø{2 * config.instrument.radius_mm:g} mm"
            " · clearance NOT ASSESSED. Save preserves this path.")

    def export_plan(self):
        if self.document is None:
            return
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export user-defined plan", f"{self.case.case_id}-plan.json", "JSON (*.json)")
        if not filename:
            return
        from skullbase_corridor.analysis.intended import assess_intended_path
        from skullbase_corridor.export.json import atomic_json_write
        try:
            atomic_json_write(Path(filename), {
                "plan_schema_version": "1",
                "review_document": self.document.model_dump(mode="json"),
                "intended_path_status": "unassessed",
                "supplied_geometry_checks": [
                    assess_intended_path(self.case, path,
                                         self.case_path.parent if self.case_path else None)
                    for path in self.document.intended_paths
                ],
                "warning": "User-defined geometry, not a surgical recommendation. "
                           "Intended paths have not been collision-tested.",
                "input_manifest": input_manifest(self.case, self.case_path.parent if self.case_path else None),
            })
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.critical(self, "Plan export failed", str(exc))

    def check_intended_path(self):
        if self.document is None:
            return
        path = next((p for p in self.document.intended_paths
                     if p.approach_name == self.intended_approach.currentData()), None)
        if path is None:
            self.target_inspection.setText("Draw an intended path for this approach first.")
            return
        from skullbase_corridor.analysis.intended import assess_intended_path
        report = assess_intended_path(self.case, path, self.case_path.parent if self.case_path else None)
        self.viewer.inspect_intended_path(path)
        self.target_inspection.setText(
            f"{report['status'].replace('_', ' ').capitalize()}\n"
            f"{report['depth_mm']:.1f} mm insertion · Ø {report['shaft_diameter_mm']:g} mm\n"
            f"{len(report['unassessed_structures'])} unassessed structures"
            + (f" · {len(report['blockers'])} blockers" if report["blockers"] else ""))
        self.target_inspection.setToolTip(
            "Unassessed: " + ", ".join(report["unassessed_structures"])
            + "\nBlocked: " + ", ".join(report["blockers"]) + "\n" + report["warning"])
        return report

    def save_edited_mask(self) -> None:
        if self.mask_editor is None or not self.case:
            return
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save corrected mask", "corrected-mask.nii.gz", "NIfTI (*.nii.gz)"
        )
        if not filename:
            return
        roles = ["Target"] + [s.name for s in self.case.protected_structures
                              if s.geometry is not None and hasattr(s.geometry, "uri")]
        role, ok = QtWidgets.QInputDialog.getItem(self, "Apply correction", "Replace", roles, 0, False)
        if not ok:
            return
        try:
            import nibabel as nib

            from skullbase_corridor.io.masks import target_from_mask
            self.mask_editor.validate_for_analysis()
            volume = self.mask_editor.volume
            stride = 1
            if role == "Target":
                mask = volume.data == self.brush_label.value()
                stride = self._choose_target_stride(int(np.count_nonzero(mask)))
                if stride is None:
                    return
            image = nib.Nifti1Image(volume.data, volume.affine)
            image.header.set_xyzt_units("mm")
            nib.save(image, filename)
            data = self.case.model_dump(mode="json")
            if role == "Target":
                data["target"] = target_from_mask(mask, volume.affine, stride=stride).model_dump(mode="json")
            else:
                structure = next(s for s in data["protected_structures"] if s["name"] == role)
                structure["geometry"] = {
                    "kind": "voxel", "uri": str(Path(filename).resolve()),
                    "affine": volume.affine.tolist(),
                    "foreground_values": [self.brush_label.value()],
                }
            updated = CorridorCase.model_validate(data)
            self._mask_pending = False
            self.undo_stack.push(ReplaceCaseCommand(self, self.case, updated, "Apply corrected mask"))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Mask save failed", str(exc))

    def _choose_target_stride(self, count: int) -> int | None:
        if count <= 2000:
            return 1
        mode, accepted = QtWidgets.QInputDialog.getItem(
            self, "Target representation",
            f"{count:,} target voxels. Full-volume analysis can be expensive.",
            ["Full voxel target — enables physical volume coverage",
             "Sparse preview — sample counts only, no volume estimate"], 0, False,
        )
        if not accepted:
            return None
        return max(1, int(np.ceil(count / 2000))) if mode.startswith("Sparse") else 1

    def choose_bone_removal(self) -> None:
        if not self.case:
            return
        bones = [s.name for s in self.case.protected_structures if s.tissue_type == "bone"]
        if not bones:
            QtWidgets.QMessageBox.information(self, "Bone removal", "Import a bone mask first.")
            return
        bone, ok = QtWidgets.QInputDialog.getItem(self, "Bone removal", "Bone mask", bones, 0, False)
        if not ok:
            return
        name, ok = QtWidgets.QInputDialog.getItem(
            self, "Bone removal", "Approach", [a.name for a in self.case.approaches], 0, False)
        if not ok:
            return
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Reviewed removal subset", "", "Mask (*.nii *.nii.gz *.nrrd)")
        if not filename:
            return
        response = QtWidgets.QMessageBox.question(
            self, "Review preparation", "Have you inspected this removal mask and confirmed it "
            "contains only intended removable bone? It must be a subset of the selected bone mask.")
        if response != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            from skullbase_corridor.domain.models import VoxelGeometry
            from skullbase_corridor.geometry.voxel import VoxelMaskBackend
            from skullbase_corridor.io.volumes import load_volume
            volume = load_volume(filename)
            removal = VoxelGeometry(uri=str(Path(filename).resolve()), affine=volume.affine.tolist())
            structure = next(s for s in self.case.protected_structures if s.name == bone)
            VoxelMaskBackend(structure.geometry, removal=removal, allow_bone_removal=True,
                             base_directory=self.case_path.parent if self.case_path else None)
            data = self.case.model_dump(mode="json")
            next(s for s in data["protected_structures"] if s["name"] == bone)["allow_virtual_removal"] = True
            approach = next(a for a in data["approaches"] if a["name"] == name)
            approach["virtual_bone_removals"] = [
                r for r in approach["virtual_bone_removals"] if r["structure_name"] != bone
            ] + [{"structure_name": bone, "geometry": removal.model_dump(mode="json"), "reviewed": True}]
            updated = CorridorCase.model_validate(data)
            self.undo_stack.push(ReplaceCaseCommand(self, self.case, updated, "Review virtual bone removal"))
            self.viewer.set_mask("removal", volume)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Invalid removal", str(exc))

    def choose_mri(self) -> None:
        if not self.case or not self.case.source_image:
            return
        proposal, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Reviewed MRI registration", "", "Registration (*.registration.json)")
        if not proposal:
            return
        output, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save CT-space MRI", "registered-mri.nii.gz", "NIfTI (*.nii.gz)")
        if not output:
            return
        try:
            from skullbase_corridor.export.json import file_sha256
            from skullbase_corridor.io.registration import resample_reviewed_mri
            from skullbase_corridor.io.volumes import load_volume
            record = json.loads(Path(proposal).read_text())
            ct = Path(self.case.source_image.uri)
            if not ct.is_absolute():
                ct = (self.case_path.parent if self.case_path else Path.cwd()) / ct
            if record["fixed_ct_sha256"] != file_sha256(ct):
                raise ValueError("Registration belongs to a different CT")
            resample_reviewed_mri(proposal, output)
            self.viewer.set_mri_overlay(load_volume(output))
            self.document.registration = record
            self.document.registration["overlay_uri"] = str(Path(output).resolve())
            self.document.registration["overlay_sha256"] = file_sha256(Path(output))
            self.document.record("reviewed_mri_overlay", self.reviewer.text())
            self._dirty = True
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "MRI overlay rejected", str(exc))

    def approve_anatomy(self) -> None:
        if not self.document:
            return
        if any(s.status.value != "known" for s in self.case.protected_structures):
            QtWidgets.QMessageBox.warning(
                self, "Incomplete anatomy",
                "Required anatomy remains unknown. Import and explicitly review required structures first."
            )
            return
        try:
            self.document.approve_anatomy(self.reviewer.text())
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Review", str(exc))
            return
        self._dirty = True
        self.anatomy_status.setText("Reviewer confirmed this configuration; not clinical validation.")

    def choose_snapshot(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save workspace snapshot", "corridor-workspace.png", "PNG (*.png)"
        )
        if filename and not self.grab().save(filename):
            QtWidgets.QMessageBox.warning(self, "Snapshot", "Could not write snapshot.")

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #151a21; color: #e6edf3; }
            QToolBar { background: #0d1117; border-bottom: 1px solid #30363d; spacing: 9px; }
            QPushButton { background: #21262d; border: 1px solid #484f58;
                          border-radius: 4px; padding: 7px 10px; }
            QPushButton:hover { background: #30363d; }
            QPushButton:disabled { color: #6e7681; }
            QPushButton#primaryButton { background: #1f6feb; font-weight: 600; }
            QTableWidget { background: #0d1117; alternate-background-color: #161b22;
                           border: 1px solid #30363d; gridline-color: #30363d; }
            QHeaderView::section { background: #21262d; padding: 6px; border: 0; }
            QGroupBox { border: 1px solid #30363d; margin-top: 12px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QDoubleSpinBox, QSpinBox { background: #0d1117; border: 1px solid #484f58;
                                      padding: 4px; }
            QLabel#landingTitle { font-size: 24px; font-weight: 650; }
            QLabel#panelTitle, QLabel#planeHeading { font-weight: 600; padding: 6px; }
            QLabel#muted { color: #8b949e; }
            QLabel#warningText { color: #d29922; padding: 16px; }
            QLabel#researchBanner { background: #2d2110; color: #e3b341; padding: 6px; }
            QLabel#pendingStatus { background: #21262d; padding: 8px; border-radius: 4px; }
            QFrame#planningInspector { background: #19212b; border-left: 1px solid #303c4a; }
            QLabel#inspectorStatus { background: #242e3a; padding: 10px; border-radius: 5px; }
            QComboBox { padding: 6px; border: 1px solid #3c4958; border-radius: 4px; }
            QLabel#assumptionBox { color: #8b949e; border-top: 1px solid #30363d; padding: 10px; }
            QStatusBar { background: #0d1117; color: #8b949e; }
            """
        )

    def _set_enabled(self, enabled: bool) -> None:
        for action in (self.save_action, self.export_action, self.analyze_action):
            action.setEnabled(enabled)
        self.run_button.setEnabled(enabled)

    def open_synthetic(self) -> None:
        if not self._allow_replace():
            return
        if self.thread:
            self.cancel_analysis()
        self.case_path = None
        self.document = None
        self.mask_editor = None
        self._mask_pending = False
        self.undo_stack.clear()
        self._set_case(analytical_case(), from_history=True)
        self.start_analysis()

    def open_planning_phantom(self) -> None:
        """Open explicitly synthetic, fully segmented teaching/verification data."""
        if not self._allow_replace():
            return
        if self.thread is not None:
            self.statusBar().showMessage("Cancel or finish analysis before opening the phantom.")
            return
        from skullbase_corridor.synthetic.planning import write_planning_phantom
        directory = Path(QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.StandardLocation.AppLocalDataLocation)) / "planning-phantom-v1"
        try:
            path = write_planning_phantom(directory)
            self._dirty = False
            self.open_case(path)
            self.start_analysis()
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.critical(self, "Planning phantom failed", str(exc))

    def choose_case(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open corridor case", "", "Corridor case (*.json)"
        )
        if filename:
            self.open_case(Path(filename))

    def open_case(self, path: Path) -> None:
        if not self._allow_replace():
            return
        try:
            case = read_case(path)
            if case.coordinate_frame != "RAS":
                raise ValueError("Desktop cases must be explicitly converted to RAS mm before opening")
            raw = json.loads(path.read_text())
            document = ReviewDocument.model_validate(raw) if "document_version" in raw else ReviewDocument(
                case=case, source_metadata={
                    key: raw[key] for key in ("real_case_metadata", "synthetic_metadata") if key in raw
                })
            # Resolve all references at open time so Save As cannot retarget them.
            data = case.model_dump(mode="json")
            references = []
            if data["source_image"]:
                references.append(data["source_image"])
            references.extend(s["geometry"] for s in data["protected_structures"]
                              if s["geometry"] and "uri" in s["geometry"])
            references.extend(r["geometry"] for a in data["approaches"]
                              for r in a["virtual_bone_removals"])
            for reference in references:
                if not Path(reference["uri"]).is_absolute():
                    reference["uri"] = str((path.resolve().parent / reference["uri"]).resolve())
            resolved = CorridorCase.model_validate(data)
            if resolved != case:
                document.replace_case(resolved, "Resolve input references")
            case = resolved
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not open case", str(exc))
            return
        self.case_path = path.resolve()
        self.document = document
        self.mask_editor = None
        self._mask_pending = False
        self.undo_stack.clear()
        self._set_case(case, from_history=True)
        self.settings.setValue("recentCase", str(self.case_path))
        self._remember_case()
        self._dirty = False

    def _set_case(self, case: CorridorCase, *, from_history: bool) -> None:
        previous_source = self.case.source_image if self.case else None
        previous_id = self.case.case_id if self.case else None
        previous_structures = self.case.protected_structures if self.case else []
        self._generation += 1
        if self.worker:
            self.worker.cancel()
        if self.document is None:
            self.document = ReviewDocument(case=case)
        elif self.document.case != case:
            self.document.replace_case(case)
        self.case = case
        self._path_entry = None
        self.intended_approach.clear()
        for a in case.approaches:
            self.intended_approach.addItem(
                "EEA · endonasal" if a.kind.value == "eea" else "CTM · transmaxillary", a.name)
        self._dirty = True
        self.result = None
        self.stack.setCurrentIndex(1)
        self._set_enabled(True)
        self.case_label.setText(case.case_id)
        metadata = self.document.source_metadata
        demo = metadata.get("real_case_metadata", metadata.get("synthetic_metadata", {}))
        self.viewer.demo_target_label = (
            "DEMONSTRATION ROI — NOT TUMOR" if demo.get("target_is_tumor") is False
            else "SYNTHETIC TARGET" if demo.get("synthetic") else "TARGET"
        )
        self.plan_context.setText(
            "Real CT · demonstration ROI, not tumor\nEntries are unreviewed examples."
            if demo.get("target_is_tumor") is False else "User-defined target and entry portals")
        self._rebuild_controls()
        self._image_error = False
        preserve_volume = (previous_source == case.source_image and previous_id == case.case_id
                           and self.viewer.source_volume is not None)
        if not preserve_volume and hasattr(self.viewer, "clear_volume"):
            self.viewer.clear_volume()
        self.viewer.set_case(case)
        self.viewer.set_intended_paths(self.document.intended_paths)
        if case.source_image is not None and not preserve_volume:
            image_path = Path(case.source_image.uri)
            if not image_path.is_absolute() and self.case_path is not None:
                image_path = self.case_path.parent / image_path
            try:
                self.viewer.load_volume(image_path)
            except Exception as exc:
                self._image_error = True
                self.statusBar().showMessage(f"Image unavailable: {exc}")
            else:
                from skullbase_corridor.io.volumes import load_volume
                if self.document.registration and "overlay_uri" in self.document.registration:
                    try:
                        from skullbase_corridor.export.json import file_sha256
                        record = self.document.registration
                        overlay = Path(record["overlay_uri"])
                        if record["overlay_sha256"] != file_sha256(overlay):
                            raise ValueError("Saved MRI overlay changed after review")
                        self.viewer.set_mri_overlay(load_volume(overlay))
                    except Exception as exc:
                        self.statusBar().showMessage(f"MRI overlay unavailable: {exc}")
        if self.viewer.source_volume is not None:
            from skullbase_corridor.io.volumes import Volume, load_volume
            current = {s.name: s for s in case.protected_structures}
            for structure in previous_structures:
                if structure.name not in current and structure.name in self.viewer.masks:
                    self.viewer.set_mask(structure.name, None)
            old = {s.name: s for s in previous_structures}
            for structure in case.protected_structures:
                if preserve_volume and old.get(structure.name) == structure:
                    continue
                geometry = structure.geometry
                if structure.name in self.viewer.masks:
                    self.viewer.set_mask(structure.name, None)
                if geometry is None or not hasattr(geometry, "uri"):
                    continue
                try:
                    mask_path = Path(geometry.uri)
                    if not mask_path.is_absolute():
                        mask_path = (self.case_path.parent if self.case_path else Path.cwd()) / mask_path
                    if mask_path.suffix == ".npy":
                        mask = Volume(np.load(mask_path, allow_pickle=False), np.asarray(geometry.affine), "label")
                    else:
                        mask = load_volume(mask_path)
                    foreground = (np.isin(mask.data, geometry.foreground_values)
                                  if geometry.foreground_values is not None else mask.data != 0)
                    self.viewer.set_mask(structure.name, Volume(foreground.astype(np.uint8), mask.affine, "label"))
                except Exception as exc:
                    self.statusBar().showMessage(f"Mask {structure.name} unavailable: {exc}")
        self._mark_stale()
        self.anatomy_status.setText(
            "Anatomy review recorded for this configuration" if self.document.anatomy_approved
            else "Anatomy not reviewed — geometric results are provisional"
        )

    def _clear_layout(self, layout: QtWidgets.QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _rebuild_controls(self) -> None:
        if self.case is None:
            return
        self._building_controls = True
        self._clear_layout(self.approach_layout)
        for index, approach in enumerate(self.case.approaches):
            group = QtWidgets.QGroupBox(approach.name)
            form = QtWidgets.QFormLayout(group)
            visible = QtWidgets.QCheckBox("Show in views")
            visible.setChecked(True)
            visible.setProperty("approach_name", approach.name)
            visible.toggled.connect(self._refresh_visibility)
            form.addRow(visible)
            fields = (
                ("Portal radius", "portal.radius_mm", approach.portal.radius_mm, 0.1, 50.0, " mm"),
                ("Instrument radius", "instrument.radius_mm", approach.instrument.radius_mm, 0.0, 10.0, " mm"),
                ("Instrument length", "instrument.length_mm", approach.instrument.length_mm, 1.0, 300.0, " mm"),
                ("Tip working radius", "instrument.tip_working_radius_mm", approach.instrument.tip_working_radius_mm, 0.0, 25.0, " mm"),
                ("Maximum angle", "sampling.max_angle_deg", approach.sampling.max_angle_deg, 1.0, 89.0, "°"),
            )
            for label, field, value, minimum, maximum, suffix in fields:
                spin = QtWidgets.QDoubleSpinBox()
                spin.setRange(minimum, maximum)
                spin.setDecimals(2)
                spin.setSuffix(suffix)
                spin.setValue(value)
                spin.setProperty("approach_index", index)
                spin.setProperty("field_path", field)
                spin.editingFinished.connect(self._control_edited)
                form.addRow(label, spin)
            expert = QtWidgets.QGroupBox("Expert geometry / sampling")
            expert.setCheckable(True)
            expert.setChecked(False)
            expert_layout = QtWidgets.QVBoxLayout(expert)
            expert_body = QtWidgets.QWidget()
            expert_form = QtWidgets.QFormLayout(expert_body)
            expert_body.setVisible(False)
            expert.toggled.connect(expert_body.setVisible)
            expert_layout.addWidget(expert_body)
            for field, values in (
                ("portal.center_mm", approach.portal.center_mm),
                ("portal.normal", approach.portal.normal),
                ("nominal_direction", approach.nominal_direction),
            ):
                for axis, value in enumerate(values):
                    spin = QtWidgets.QDoubleSpinBox()
                    spin.setRange(-2000, 2000)
                    spin.setDecimals(3)
                    spin.setValue(value)
                    spin.setProperty("approach_index", index)
                    spin.setProperty("field_path", f"{field}.{axis}")
                    spin.editingFinished.connect(self._control_edited)
                    expert_form.addRow(f"{field} {'XYZ'[axis]}", spin)
            for name, maximum in (("polar_steps", 100), ("azimuth_steps", 720),
                                  ("adaptive_levels", 4), ("portal_offset_rings", 4)):
                spin = QtWidgets.QSpinBox()
                spin.setRange(0 if name in ("adaptive_levels", "portal_offset_rings") else 1, maximum)
                spin.setValue(getattr(approach.sampling, name))
                spin.setProperty("approach_index", index)
                spin.setProperty("field_path", f"sampling.{name}")
                spin.editingFinished.connect(self._control_edited)
                expert_form.addRow(name, spin)
            form.addRow(expert)
            self.approach_layout.addWidget(group)
        self._building_controls = False

    def _refresh_visibility(self) -> None:
        if self.case is None:
            return
        visible = []
        for checkbox in self.approach_box.findChildren(QtWidgets.QCheckBox):
            if checkbox.isChecked():
                visible.append(checkbox.property("approach_name"))
        self.viewer.set_case(self.case, visible)

    def _control_edited(self) -> None:
        if self._building_controls or self.case is None:
            return
        spin = self.sender()
        index = int(spin.property("approach_index"))
        path = str(spin.property("field_path"))
        data = self.case.model_dump(mode="json")
        cursor: dict[str, Any] = data["approaches"][index]
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
        key = int(parts[-1]) if isinstance(cursor, list) else parts[-1]
        old_value = cursor[key]
        if abs(float(old_value) - spin.value()) < 1e-9:
            return
        cursor[key] = spin.value()
        try:
            updated = CorridorCase.model_validate(data)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(exc))
            self._rebuild_controls()
            return
        self.undo_stack.push(
            ReplaceCaseCommand(self, self.case, updated, f"Change {path}")
        )

    def _mark_stale(self) -> None:
        self.result = None
        self._analysis_inputs = None
        self.path_selector.clear()
        self.path_controls.setEnabled(False)
        self.comparison_panel.set_case(self.case)
        self.comparison_panel.set_result(None)
        self.coverage_strip.setText("Coverage unavailable — run analysis for this configuration.")
        self.target_inspection.setText(
            "Click a displayed target sample to inspect the paths that reach it.")
        self.results.setRowCount(0)
        self.result_status.setText("Results pending — run analysis for this configuration.")
        self.combination_text.setText("Combined access: unavailable until analysis")
        self.export_action.setEnabled(False)
        self.witness_details.clear()
        if hasattr(self.viewer, "set_result"):
            self.viewer.set_result(None)

    def start_analysis(self) -> None:
        if self.case is None or self.thread is not None:
            return
        if self._mask_pending:
            self.result_status.setText("Save/apply edited mask before analysis.")
            return
        if self._image_error:
            self.result_status.setText("Source image failed to load. Resolve it before analysis.")
            return
        self.run_button.setEnabled(False)
        self.analyze_action.setEnabled(False)
        self.cancel_action.setEnabled(True)
        self.open_action.setEnabled(False)
        self.synthetic_action.setEnabled(False)
        self.result_status.setText("Calculating sampled geometric access…")
        self.thread = QtCore.QThread(self)
        self.worker = AnalysisWorker(
            self.case,
            self.case_path.parent if self.case_path else None,
            self._generation,
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.completed.connect(self._analysis_completed)
        self.worker.failed.connect(self._analysis_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._analysis_finished)
        self.thread.start()

    def cancel_analysis(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.result_status.setText("Cancellation requested…")

    @QtCore.Slot(object, int)
    def _analysis_completed(self, result, generation: int) -> None:
        if generation != self._generation:
            self.result_status.setText(
                "Results pending — configuration changed during analysis."
            )
            return
        self.result = result
        self.side_tabs.setCurrentIndex(1)
        self._analysis_inputs = self.worker.inputs if self.worker else None
        if hasattr(self.viewer, "set_result"):
            self.viewer.set_result(result)
        self.comparison_panel.set_case(self.case)
        self.comparison_panel.set_result(result)
        comparison = self.comparison_panel.comparison
        self.viewer.set_coverage_labels([
            "unreached" if category.value == "not_reached" else category.value
            for category in comparison.categories
        ])
        def membership_text(values):
            reached = sum(v is True for v in values)
            return f"≥{reached}" if any(v is None for v in values) else str(reached)
        union = [
            True if a is True or b is True else None if a is None or b is None else False
            for a, b in zip(comparison.eea_reached, comparison.tm_reached)
        ]
        self.coverage_strip.setText(
            f"Sampled target coverage · EEA {membership_text(comparison.eea_reached)}"
            f" · Transmaxillary {membership_text(comparison.tm_reached)}"
            f" · Combined {membership_text(union)} / {comparison.target_count}"
            f" · TM adds {'≥' if not comparison.tm_incremental_complete else ''}"
            f"{comparison.tm_incremental_count}"
            f" · Unavailable comparisons {comparison.counts['unavailable']}"
            " | Geometric access, not resection or approach necessity."
        )
        if comparison.tm_incremental_volume_mm3 is not None:
            self.coverage_strip.setText(
                self.coverage_strip.text()
                + f" TM incremental target volume: {comparison.tm_incremental_volume_mm3:.1f} mm³."
            )
        self._populate_paths()
        self.results.setRowCount(len(result.approaches))
        for row, approach in enumerate(result.approaches):
            unavailable = approach.status.value in ("abstained", "incomplete", "unsupported")
            reached = "Unavailable" if unavailable else f"{len(approach.reached_point_indices)}/{approach.target_count}"
            values = (
                approach.name,
                approach.status.value.replace("_", " "),
                reached,
                "—" if unavailable or approach.feasible_solid_angle_sr is None else f"{approach.feasible_solid_angle_sr:.3f} sr",
                "—" if approach.best_working_depth_mm is None else f"{approach.best_working_depth_mm:.1f} mm",
                "—" if approach.minimum_clearance_mm is None else f"{approach.minimum_clearance_mm:.1f} mm",
            )
            for column, value in enumerate(values):
                self.results.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        summaries = []
        for combo in result.combinations:
            if combo.union_indices is None or getattr(combo, "status", "") in ("abstained", "incomplete", "unsupported"):
                summaries.append(f"{combo.first} + {combo.second}: unavailable; anatomy incomplete")
                continue
            summaries.append(
                f"{combo.first} + {combo.second}: union {len(combo.union_indices)}, "
                f"overlap {len(combo.intersection_indices)}, incremental "
                f"{len(combo.incremental_first_indices)} / {len(combo.incremental_second_indices)}"
            )
        for pair in result.simultaneous_pairs:
            summaries.append(
                f"Simultaneous {pair.first} + {pair.second}: "
                f"{'unavailable' if pair.feasible is None else 'feasible under configured model' if pair.feasible else 'not found at this resolution'}"
            )
        self.combination_text.setText("\n".join(summaries) or "No approach comparison")
        self.result_status.setText(
            "Calculation finished with unavailable measurements — required anatomy is incomplete."
            if any(a.status.value in ("abstained", "incomplete", "unsupported") for a in result.approaches)
            else "Calculation complete. Sampled geometric result; inspect assumptions and witnesses."
        )
        self.export_action.setEnabled(True)
        if result.approaches:
            self.results.selectRow(0)

    def _show_witness(self, row: int, *_args) -> None:
        if self.result is None or not 0 <= row < len(self.result.approaches):
            return
        approach = self.result.approaches[row]
        for index in range(self.path_selector.count()):
            data = self.path_selector.itemData(index)
            if data and data[0] == approach.name:
                self.path_selector.setCurrentIndex(index)
                break
        self.witness_details.setPlainText(json.dumps({
            "witness_direction": approach.witness_direction,
            "polar_deg": approach.witness_polar_angle_deg,
            "azimuth_deg": approach.witness_azimuth_deg,
            "sample_count": len(approach.trajectories),
            "notes": approach.notes,
        }, indent=2))

    def _populate_paths(self, target_index: int | None = None) -> None:
        """Only selectable, unconditional feasible paths from the current result."""
        self.viewer.inspected_target_index = target_index
        selected = getattr(self.viewer, "selected_trajectory", None)
        self.path_selector.blockSignals(True)
        self.path_selector.clear()
        if self.result is not None:
            for approach in self.result.approaches:
                if approach.name not in self.viewer.visible:
                    continue
                valid_indices = {
                    path.trajectory_index
                    for path in self.viewer.available_trajectories(approach.name)
                }
                for index, trajectory in enumerate(approach.trajectories):
                    if index not in valid_indices:
                        continue
                    if target_index is not None and target_index not in trajectory.reached_point_indices:
                        continue
                    self.path_selector.addItem(
                        f"{approach.name} · path {index + 1} · "
                        f"{trajectory.working_depth_mm:.1f} mm · "
                        f"{len(trajectory.reached_point_indices)} targets",
                        (approach.name, index),
                    )
        self.path_selector.blockSignals(False)
        self.path_controls.setEnabled(self.path_selector.count() > 0)
        if self.path_selector.count():
            initial = 0
            if selected is not None:
                for index in range(self.path_selector.count()):
                    if self.path_selector.itemData(index) == (
                            selected.approach_name, selected.trajectory_index):
                        initial = index
                        break
            self.path_selector.setCurrentIndex(initial)
            self._select_path(initial)
        else:
            if self.case is not None:
                self.viewer.select_trajectory("", None)
            self.path_hint.setText(
                "No unconditional feasible trajectory is available. "
                "This does not certify that no surgical route exists.")

    def _select_path(self, index: int) -> None:
        data = self.path_selector.itemData(index)
        if not data or self.result is None:
            return
        self.viewer.select_trajectory(data[0], data[1])
        self.viewer.detail_tabs.setCurrentWidget(self.viewer.path_view)
        self.viewer.support_tabs.setCurrentWidget(self.viewer.detail_tabs)
        self.path_hint.setText(
            "Dashed: path projected onto this CT plane. Solid: path within the slice. "
            "Use the path-aligned CT view to see the entry-to-tip route in one plane.")

    def _focus_path(self) -> None:
        if self.result is not None and self.path_selector.currentData():
            self.viewer.focus_selected_trajectory()

    def _clear_target_filter(self) -> None:
        self.target_inspection.setText(
            "All feasible paths. Click a displayed target sample to filter by reach.")
        self._populate_paths()

    def _approach_display_changed(self) -> None:
        if hasattr(self, "path_selector"):
            self._populate_paths(getattr(self.viewer, "inspected_target_index", None))

    def _inspect_target(self, point) -> None:
        if self.case is None or self.result is None:
            return
        points = self.case.target.array()
        distances = np.linalg.norm(points - np.asarray(point), axis=1)
        index = int(np.argmin(distances))
        # Use a small physical hit radius; never silently select a remote target.
        if distances[index] > 3.0:
            return
        comparison = self.comparison_panel.comparison
        category = comparison.categories[index].value.replace("_", " ")
        self._populate_paths(index)
        self.target_inspection.setText(
            f"Target sample {index + 1} · {category} · "
            f"{self.path_selector.count()} unconditional feasible paths. "
            "No path found does not prove inaccessibility."
        )

    @QtCore.Slot(str)
    def _analysis_failed(self, message: str) -> None:
        self.result_status.setText("Analysis failed")
        QtWidgets.QMessageBox.critical(self, "Analysis failed", message)

    @QtCore.Slot()
    def _analysis_finished(self) -> None:
        if self.thread:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None
        self.run_button.setEnabled(self.case is not None)
        self.analyze_action.setEnabled(self.case is not None)
        self.cancel_action.setEnabled(False)
        self.open_action.setEnabled(True)
        self.synthetic_action.setEnabled(True)
        if self.result is None:
            self.result_status.setText("No current result — cancelled, failed, or configuration changed.")
        if self._close_after_worker:
            QtCore.QTimer.singleShot(0, self.close)

    def save_case(self) -> None:
        if self.case is None:
            return
        if self._mask_pending:
            QtWidgets.QMessageBox.warning(
                self, "Unapplied mask corrections",
                "Save/apply the edited mask first. Case Save does not contain unsaved voxel edits.")
            return
        path = self.case_path
        if path is None:
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save corridor case", f"{self.case.case_id}.json", "JSON (*.json)"
            )
            if not filename:
                return
            path = Path(filename)
        try:
            assert self.document is not None
            self.document.save(path)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.case_path = path.resolve()
        self._dirty = False
        self._remember_case()
        self.settings.setValue("recentCase", str(self.case_path))
        self.statusBar().showMessage(f"Saved {path}", 5000)

    def choose_export(self) -> None:
        if self.case is None or self.result is None:
            return
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export analysis", f"{self.case.case_id}-analysis.json", "JSON (*.json)"
        )
        if filename:
            try:
                base = self.case_path.parent if self.case_path else None
                if self._analysis_inputs is not None and input_manifest(self.case, base) != self._analysis_inputs:
                    self._mark_stale()
                    raise ValueError("External input files changed; rerun analysis before export")
                export_result(
                    filename, self.case, self.result,
                    analysis_options={
                        "simultaneous_minimum_angle_deg": 20.0,
                        "anatomy_review_current": bool(self.document and self.document.anatomy_approved),
                        "reviewed_input_hashes": self.document.approved_inputs if self.document else None,
                    },
                    base_directory=base,
                )
                export_csv(Path(filename).with_suffix(".csv"), self.result)
                from skullbase_corridor.analysis.coverage import export_comparison
                export_comparison(
                    Path(filename).parent / f"{Path(filename).stem}-coverage",
                    self.case, self.result,
                )
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))
                return
            self.statusBar().showMessage(f"Exported {filename}", 5000)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.settings.setValue("windowGeometry", self.saveGeometry())
        if self.thread:
            self._close_after_worker = True
            self.cancel_analysis()
            self.statusBar().showMessage("Cancelling analysis before closing.")
            event.ignore()
            return
        if not self._allow_replace():
            self._close_after_worker = False
            event.ignore()
            return
        self.settings.setValue("axialPlanningSplitterState", self.splitter.saveState())
        super().closeEvent(event)

    def _allow_replace(self) -> bool:
        if not (self._dirty or self._mask_pending):
            return True
        answer = QtWidgets.QMessageBox.question(
            self, "Unsaved changes", "Discard unsaved case changes or unapplied mask corrections?",
            QtWidgets.QMessageBox.StandardButton.Discard | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        return answer == QtWidgets.QMessageBox.StandardButton.Discard


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Skull-Base Corridor")
    app.setOrganizationName("OpenSkullBase")
    window = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
