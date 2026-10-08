import numpy as np
import pytest

from corridorkit.anatomy.entry_proposals import (
    AnatomyMask,
    ProposalStatus,
    TargetLaterality,
    propose_entry_candidates,
)


def _world_grid(shape, affine):
    indices = np.indices(shape).reshape(3, -1).T
    points = indices @ affine[:3, :3].T + affine[:3, 3]
    return points.reshape(*shape, 3)


def _anatomy(affine, shape=(51, 51, 21)):
    world = _world_grid(shape, affine)
    x, y, z = (world[..., index] for index in range(3))
    masks = {
        "left_nasal_cavity": (x > 5) & (x < 9) & (y > 4) & (y < 15) & (abs(z) < 4),
        "right_nasal_cavity": (x > 15) & (x < 19) & (y > 4) & (y < 15) & (abs(z) < 4),
        "left_maxillary_sinus": (x > 1) & (x < 8) & (y > 3) & (y < 14) & (abs(z) < 5),
        "right_maxillary_sinus": (x > 16) & (x < 23) & (y > 3) & (y < 14) & (abs(z) < 5),
        "bone": np.ones(shape, dtype=bool),
        "hard_palate": (abs(x - 12) < 6) & (abs(y - 9) < 4) & (z < -6),
        "upper_teeth": (abs(x - 12) < 7) & (y > 14) & (z < -4),
        "left_orbit": (x > 2) & (x < 7) & (y > 7) & (abs(z - 7) < 2),
        "right_orbit": (x > 17) & (x < 22) & (y > 7) & (abs(z - 7) < 2),
        "protected": np.zeros(shape, dtype=bool),
    }
    masks["protected"][0, 0, 0] = True
    assert all(mask.any() for mask in masks.values())
    return masks


def _assert_anterior_boundary(candidate, masks, affine):
    mask_name = f"{candidate.side}_nasal_cavity"
    if candidate.approach == "ctm":
        sinus = masks[f"{candidate.side}_maxillary_sinus"]
        voxel = np.asarray(candidate.entry_voxel)
        assert masks["bone"][tuple(voxel)]
        assert not sinus[tuple(voxel)]
    else:
        sinus = None
    mask = masks[mask_name]
    voxel = np.asarray(candidate.entry_voxel)
    if candidate.approach == "eea":
        assert mask[tuple(voxel)]
    unit_columns = affine[:3, :3] / np.linalg.norm(affine[:3, :3], axis=0)
    projections = unit_columns.T @ np.array([0.0, 1.0, 0.0])
    axis = int(np.argmax(np.abs(projections)))
    neighbour = voxel.copy()
    step = 1 if projections[axis] >= 0 else -1
    if candidate.approach == "eea":
        neighbour[axis] += step
        assert not np.all((neighbour >= 0) & (neighbour < np.asarray(mask.shape))) or not mask[
            tuple(neighbour)
        ]
    else:
        neighbour[axis] -= step
        assert np.all((neighbour >= 0) & (neighbour < np.asarray(sinus.shape)))
        assert sinus[tuple(neighbour)]


def test_target_drives_laterality_and_separates_ctm_landmarks():
    affine = np.eye(4)
    affine[:3, 3] = [0, 0, -10]
    masks = _anatomy(affine)

    result = propose_entry_candidates((18, 22, 0), masks, affine)

    assert result.target_laterality == TargetLaterality.RIGHT
    assert result.contralateral_sides == ["left"]
    assert result.midsagittal is not None
    assert result.midsagittal.point_ras_mm[0] == pytest.approx(12, abs=0.6)
    assert result.status == ProposalStatus.COMPLETE
    assert {item.side for item in result.candidates if item.approach == "ctm"} == {"left"}
    assert len([item for item in result.candidates if item.approach == "eea"]) >= 4
    for candidate in result.candidates:
        _assert_anterior_boundary(candidate, masks, affine)
        if candidate.approach == "ctm":
            assert candidate.internal_medial_wall_voxel is not None
            assert candidate.internal_medial_wall_voxel != candidate.entry_voxel
            evidence = next(
                item for item in result.removal_evidence
                if item.evidence_id == candidate.removal_evidence_id
            )
            assert evidence.reviewed is False
            assert evidence.voxels
    for approach, side in {(item.approach, item.side) for item in result.candidates}:
        points = np.asarray([
            item.entry_ras_mm for item in result.candidates
            if (item.approach, item.side) == (approach, side)
        ])
        if len(points) > 1:
            assert np.min(
                np.linalg.norm(points[:, None] - points[None, :], axis=2)
                + np.eye(len(points)) * 1e6
            ) >= 3


def test_midline_target_proposes_bilateral_ctm():
    affine = np.eye(4)
    affine[:3, 3] = [0, 0, -10]
    result = propose_entry_candidates((12, 22, 0), _anatomy(affine), affine)

    assert result.target_laterality == TargetLaterality.MIDLINE
    assert result.contralateral_sides == ["left", "right"]
    assert {item.side for item in result.candidates if item.approach == "ctm"} == {
        "left", "right"
    }


@pytest.mark.parametrize(
    "affine",
    [
        np.array([[-1, 0, 0, 30], [0, 1, 0, 0], [0, 0, 1, -10], [0, 0, 0, 1.0]]),
        np.array([
            [np.cos(0.31), -np.sin(0.31), 0, 4],
            [np.sin(0.31), np.cos(0.31), 0, -4],
            [0, 0, 1, -10],
            [0, 0, 0, 1],
        ]),
    ],
)
def test_affine_mirror_and_oblique_preserve_world_anatomy(affine):
    masks = _anatomy(affine)
    result = propose_entry_candidates((18, 22, 0), masks, affine)

    assert result.target_laterality == TargetLaterality.RIGHT
    assert result.contralateral_sides == ["left"]
    assert result.midsagittal is not None
    normal = np.asarray(result.midsagittal.left_to_right_normal)
    assert normal[0] > 0.98
    for candidate in result.candidates:
        _assert_anterior_boundary(candidate, masks, affine)


def test_predicted_and_missing_optional_masks_are_explicitly_conditional():
    affine = np.eye(4)
    affine[:3, 3] = [0, 0, -10]
    masks = _anatomy(affine)
    masks.pop("upper_teeth")
    masks.pop("protected")
    masks["left_nasal_cavity"] = AnatomyMask(
        masks["left_nasal_cavity"], status="predicted"
    )

    result = propose_entry_candidates((18, 22, 0), masks, affine)

    assert result.status == ProposalStatus.CONDITIONAL
    assert any("upper_teeth" in item for item in result.missing_constraints)
    assert all(candidate.conditional_constraints for candidate in result.candidates)
    assert any(
        "predicted" in constraint
        for candidate in result.candidates
        for constraint in candidate.conditional_constraints
    )
    assert not hasattr(result, "safety_probability")


def test_abstains_without_bilateral_anatomy():
    affine = np.eye(4)
    only_left = np.zeros((9, 9, 9), dtype=bool)
    only_left[1:4, 2:7, 2:7] = True

    result = propose_entry_candidates(
        (1, 1, 1), {"left_nasal_cavity": only_left}, affine
    )

    assert result.status == ProposalStatus.ABSTAINED
    assert result.midsagittal is None
    assert result.candidates == []
    assert "bilateral-anatomy" in result.notes[0]


def test_rejects_noncommon_grid_and_invalid_affine():
    with pytest.raises(ValueError, match="common grid"):
        propose_entry_candidates(
            (0, 0, 0),
            {
                "left_nasal_cavity": np.zeros((3, 3, 3)),
                "right_nasal_cavity": np.zeros((4, 3, 3)),
            },
            np.eye(4),
        )
    with pytest.raises(ValueError, match="invertible"):
        singular = np.eye(4)
        singular[0, 0] = 0
        propose_entry_candidates(
            (0, 0, 0),
            {"left_nasal_cavity": np.ones((3, 3, 3))},
            singular,
        )
