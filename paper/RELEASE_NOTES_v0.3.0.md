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

## Verified release

- Source commit: `db27dd0fc8c4a5517d9556269fdf7202e8659ec2`.
- [Exact-tag CI](https://github.com/abachu2005/corridorkit/actions/runs/37847343247):
  all eight jobs passed, including Ubuntu, macOS, and Windows on Python 3.11/3.12.
- Clean-checkout tests: 652 passed; five tests requiring local public-data
  caches skipped. Native desktop and Slicer 5.12.3 integration passed on macOS.
- Verified wheel, source distribution, Slicer ZIP, and release evidence are
  attached below. The source tag remains unchanged after verification.
- Version DOI: [10.5281/zenodo.23249277](https://doi.org/10.5281/zenodo.23249277).

The final DOI-linked manuscripts and post-release verification evidence are
published as release assets and on `main`; they are not retroactive changes to
the archived source tag.
