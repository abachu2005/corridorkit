# Changelog

## 0.3.0 — CorridorKit

- Rename the GitHub repository, Python distribution/import package, and CLI to
  `corridorkit`; the desktop command is now `corridorkit-gui`.
- Use CorridorKit branding in the desktop and Slicer extension.
- Update packaging, runtime launchers, tests, documentation, and citation metadata.
- Retain the finite-instrument geometry model, result schema, and analysis semantics.
- Consolidate the SoftwareX manuscript, bibliography, figures, and PDF build checks.

### Migration from 0.2.1

Install the new distribution from the CorridorKit release. Replace Python imports
from `skullbase_corridor` with `corridorkit`, the `skullbase-corridor` command with
`corridorkit`, and `skullbase-corridor-gui` with `corridorkit-gui`.
Use `CORRIDORKIT_PYTHON` when explicitly configuring the external Slicer engine.
Existing case JSON remains supported; rerun analyses to record the new software
version. Historical releases and evidence keep their original identifiers.
