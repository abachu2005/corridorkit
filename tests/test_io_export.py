import json

import numpy as np
import pytest

from corridorkit.export.json import checksum, export_result, read_case, write_case
from corridorkit.geometry.engine import analyze_case
from corridorkit.io.masks import indices_to_physical, target_from_mask, voxel_volume_mm3
from corridorkit.synthetic.cases import analytical_case


def test_affine_orientation_and_anisotropic_volume():
    affine = np.array([
        [0.0, -2.0, 0.0, 10.0],
        [1.0, 0.0, 0.0, 20.0],
        [0.0, 0.0, 3.0, 30.0],
        [0.0, 0.0, 0.0, 1.0],
    ])
    point = indices_to_physical([[1, 2, 3]], affine)[0]
    assert point == pytest.approx([6, 21, 39])
    assert voxel_volume_mm3(affine) == pytest.approx(6)
    mask = np.zeros((2, 3, 4), dtype=bool)
    mask[1, 2, 3] = True
    target = target_from_mask(mask, affine)
    assert target.points_mm[0] == pytest.approx((6, 21, 39))
    assert target.point_volume_mm3 == pytest.approx(6)


def test_serialization_round_trip_and_checksums(tmp_path):
    case = analytical_case()
    case_path = tmp_path / "case.json"
    result_path = tmp_path / "result.json"
    write_case(case_path, case)
    loaded = read_case(case_path)
    assert loaded == case
    result = analyze_case(loaded)
    envelope = export_result(result_path, loaded, result)
    persisted = json.loads(result_path.read_text())
    assert persisted == envelope
    assert persisted["source_sha256"] == checksum(case.model_dump(mode="json"))
    assert persisted["result_sha256"] == checksum(result.model_dump(mode="json"))
