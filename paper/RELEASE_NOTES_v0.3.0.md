# CorridorKit v0.3.0

CorridorKit is open-source research software for geometric comparison of
skull-base surgical corridors.

This release completes the rename across the repository, Python distribution
and imports (`corridorkit`), CLI (`corridorkit`), desktop launcher
(`corridorkit-gui`), Slicer extension, and documentation. The finite-instrument
geometry model and case schema are unchanged.

## Install from source

```sh
git clone --branch v0.3.0 https://github.com/abachu2005/corridorkit.git
cd corridorkit
python -m pip install -e '.[desktop,test]'
corridorkit synthetic case.json
corridorkit analyze case.json result.json
corridorkit doctor
```

See `CHANGELOG.md` for migration from version 0.2.1 and `slicer/README.md` for
the Slicer workflow. Existing case JSON is supported; rerun analysis to record
the new software version. Public-data caches and model weights are not bundled.

Verification records are published separately from the immutable source tag.
The software is for research use; numerical and integration verification do
not constitute clinical validation.
