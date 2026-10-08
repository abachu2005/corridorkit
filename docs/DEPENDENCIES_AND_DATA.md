# Software and data notices

The project code is Apache-2.0; also see `LICENSE`. The desktop layout was
independently implemented after inspecting AutoHijdra and Spine HU; source code
from those applications was not copied. The GitHub license endpoint for
`abachu2005/spine-hu-tool` returned 404 on 2026-10-01, so reuse permission must not
be inferred from public visibility.

Runtime dependencies retain their own terms:

- NumPy, SciPy, nibabel, pydicom: BSD-family licenses.
- SimpleITK: Apache-2.0.
- Pydantic and Typer: MIT.
- pyqtgraph: MIT.
- VTK: BSD-style.
- PySide6 / Qt: LGPL/GPL/commercial options depending on module. A bundled
  distribution must include required license notices, allow replacement of
  LGPL libraries, and check the actual included Qt modules.
- PyInstaller: GPL with bootloader distribution exception.
- TotalSegmentator 2.11.0: Apache-2.0 software; its model weights and upstream
  datasets retain their stated terms. The Slicer package pins the software
  version but does not redistribute weights.
- 3D Slicer: BSD-style license. It is installed separately and is not bundled
  in the extension ZIP.

These are dependency-family notices, not a substitute for collecting the
licenses of the exact shipped binaries. Native application redistribution is
not approved merely because a build recipe exists.

Public data is not included in source or wheels. NasalSeg v2 is Zenodo record
13893419, declared CC-BY-4.0; the evaluated archive SHA-256 is
`60c6facf843685802c39e4adff4a05c081c1c4b6175c9cb573745c55abb0fa6a`.
Its source record, checksum, declared data license, and per-case provenance
belong in the frozen dataset manifest. Original v1 and v2 releases must not be
mixed silently.
Data licensing does not license clinical claims. No pretrained weights are
required or redistributed.

Local review documents contain input paths, optional reviewer identities and
registration filenames; treat them as restricted working records. JSON/CSV
analysis exports omit these paths, but case/approach identifiers can still be
identifying if users enter identifying text. Review identifiers and snapshots
before sharing. Snapshots may contain visible source information.
