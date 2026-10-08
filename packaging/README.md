# Local macOS research-alpha bundle

This is an unsigned-for-distribution developer build, not a notarized release.
The app bundles Python, Qt and VTK; it does not require the user's Anaconda
installation at runtime.

```sh
python -m pip install -e ".[desktop]" pyinstaller pillow
python packaging/build_macos.py
python packaging/verify_macos.py
```

PyInstaller can return exit code zero after a bundle-signing warning. The
verifier treats a missing/invalid seal as failure. If the local ad-hoc signing
step reports a transient Code Signing subsystem error, repair and recheck it:

```sh
codesign --force --deep --sign - --timestamp=none dist/CorridorKit.app
codesign --verify --deep --strict dist/CorridorKit.app
python packaging/verify_macos.py --output research/native-bundle-evidence/new-verification
```

This uses an ad-hoc identity, not a paid certificate or notarization service.

Allow at least 5 GiB free before building. The build excludes alternative Qt
bindings and unrelated optional scientific/notebook stacks so a broad
development environment cannot introduce multiple Qt runtimes.

Output: `dist/CorridorKit.app`.

The verifier runs the actual frozen executable outside the source checkout with
Python, Conda, Qt and dynamic-library environment overrides removed. It requires
a synthetic calculation, native VTK rendering, screenshots, JSON export,
session save/reopen, CT import and abstention for unreviewed anatomy. It also
checks the local ad-hoc code signature and the embedded source SHA-256 manifest
against the checkout. Reports are saved to
`research/native-bundle-evidence/frozen`; use `--output` with a fresh directory
for repeat runs so previous evidence is not mistaken for a new success.

These checks do not replace clean-machine testing, Developer ID signing,
notarization, independent human acceptance or operative reference validation.
Windows/Linux native packages and hosted CI must be verified on those platforms.
See `docs/HUMAN_ACCEPTANCE.md` and `docs/RELEASE_GATES.md`.
