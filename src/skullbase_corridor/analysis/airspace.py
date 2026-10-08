"""Independent, scan-derived computational geometry; no surgical anatomy claims."""
from __future__ import annotations

import itertools
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

CONFIG = {
    "protocol": "nasalseg-airspace-v1",
    "split_salt": "nasalseg-airspace-v1-20261001",
    "development_fraction": 0.6, "validation_fraction": 0.2,
    "radii_mm": [0.0, 0.5, 1.0], "lengths_mm": [10.0, 30.0, 60.0],
    "interior_distance_mm": 2.0, "clearance_ceiling_mm": 2.0,
    "scenarios": [
        {"name": "baseline", "translation_ras_mm": [0.0, 0.0, 0.0], "boundary_inflation_mm": 0.0},
        {"name": "registration_x_plus_1mm", "translation_ras_mm": [1.0, 0.0, 0.0], "boundary_inflation_mm": 0.0},
        {"name": "registration_x_minus_1mm", "translation_ras_mm": [-1.0, 0.0, 0.0], "boundary_inflation_mm": 0.0},
        {"name": "segmentation_boundary_plus_1mm", "translation_ras_mm": [0.0, 0.0, 0.0], "boundary_inflation_mm": 1.0},
    ],
    "bootstrap_repeats": 5000, "seed": 20261001,
    "geometry_tolerance_mm": 1e-8,
}


def physical(indices: np.ndarray, affine: np.ndarray) -> np.ndarray:
    return indices @ affine[:3, :3].T + affine[:3, 3]


def reference_clearance(start, end, centers, obstacle_radius, ceiling=2.0):
    """Exhaustive projection reference; intentionally does not call fast path."""
    vector = end - start
    denominator = sum(float(x) ** 2 for x in vector)
    offset = centers - start
    fraction = np.clip(np.sum(offset * vector, axis=1) / denominator, 0, 1)
    residual = offset - fraction[:, None] * vector
    distances = np.sqrt(np.sum(residual**2, axis=1))
    return float(min(ceiling, np.min(distances) - obstacle_radius))


def accelerated_clearance(start, end, centers, tree, obstacle_radius, ceiling=2.0):
    """KD-tree broad phase plus cross-product point/segment distance."""
    vector = end - start
    length = float(np.linalg.norm(vector))
    midpoint = (start + end) / 2
    indices = tree.query_ball_point(midpoint, length / 2 + obstacle_radius + ceiling + 1e-9)
    if not indices:
        return ceiling
    points = centers[indices]
    relative = points - start
    along = np.sum(relative * (vector / length), axis=1)
    distance = np.linalg.norm(np.cross(relative, vector / length), axis=1)
    before, after = along < 0, along > length
    distance[before] = np.linalg.norm(points[before] - start, axis=1)
    distance[after] = np.linalg.norm(points[after] - end, axis=1)
    return float(min(ceiling, np.min(distance) - obstacle_radius))


def contained(start, end, affine, shape, margin_mm):
    inverse = np.linalg.inv(affine)
    indices = physical(np.asarray([start, end]), inverse)
    # Exact physical sphere extent in index coordinates, also valid for shears.
    index_margin = margin_mm * np.linalg.norm(inverse[:3, :3], axis=1)
    return bool(np.all(indices - index_margin >= -0.5) and
                np.all(indices + index_margin <= np.asarray(shape) - 0.5))


def evaluate_airspace(label: np.ndarray, affine: np.ndarray) -> dict:
    spacing = np.linalg.norm(affine[:3, :3], axis=0)
    unit = affine[:3, :3] / spacing
    if not np.allclose(unit.T @ unit, np.eye(3), atol=1e-6, rtol=0):
        return {"status": "excluded", "exclusion_reasons": ["nonorthogonal_grid_unsupported"]}
    targets, label_records, anchors = [], [], []
    for value in range(1, 6):
        mask = label == value
        if not np.any(mask):
            label_records.append({"label": value, "status": "excluded", "reason": "label_absent"})
            continue
        distance = ndimage.distance_transform_edt(np.pad(mask, 1), sampling=spacing)[1:-1, 1:-1, 1:-1]
        eligible = np.argwhere(mask & (distance >= CONFIG["interior_distance_mm"]))
        if len(eligible) < 3:
            label_records.append({"label": value, "status": "excluded", "reason": "fewer_than_three_2mm_interior_centers"})
            continue
        deepest = np.asarray(np.unravel_index(np.argmax(distance), distance.shape))
        selected = sorted({tuple(deepest), tuple(eligible[0]), tuple(eligible[-1])})
        label_records.append({
            "label": value, "status": "included", "eligible_center_count": len(eligible),
            "selected_indices": [list(map(int, index)) for index in selected],
            "deepest_interior_distance_mm": float(distance[tuple(deepest)]),
        })
        anchors.append({"label": value, "index": deepest.tolist(), "point_ras_mm": physical(deepest, affine).tolist()})
        for index in selected:
            targets.append({"label": value, "index": list(map(int, index)), "point_ras_mm": physical(np.asarray(index), affine).tolist()})
    if not targets:
        return {"status": "excluded", "exclusion_reasons": ["no_verified_interior_targets"], "labels": label_records}
    anchors = [anchors[0]] if len(anchors) == 1 else [anchors[0], anchors[-1]]
    air = label > 0
    boundary = ndimage.binary_dilation(air, structure=ndimage.generate_binary_structure(3, 1)) & ~air
    centers = physical(np.argwhere(boundary), affine)
    if not len(centers):
        return {"status": "excluded", "exclusion_reasons": ["no_observed_boundary_obstacles"], "labels": label_records}
    base_radius = float(np.linalg.norm(spacing) / 2)
    trajectories, skipped = [], []
    for anchor_index, anchor in enumerate(anchors):
        start = np.asarray(anchor["point_ras_mm"])
        for target_index, target in enumerate(targets):
            end = np.asarray(target["point_ras_mm"])
            length = float(np.linalg.norm(end - start))
            if length < 1e-9:
                skipped.append({"anchor": anchor_index, "target": target_index, "reason": "self_segment"})
                continue
            trajectories.append({"anchor": anchor_index, "target": target_index, "length_mm": length, "scenarios": []})
    if not trajectories:
        return {"status": "excluded", "exclusion_reasons": ["no_nonself_segments"], "labels": label_records}
    counts = {scenario["name"]: {f"r{r:g}_l{l:g}": {"reached": 0, "blocked": 0, "abstained": 0}
              for r, l in itertools.product(CONFIG["radii_mm"], CONFIG["lengths_mm"])}
              for scenario in CONFIG["scenarios"]}
    errors, false_feasible, mismatches = [], 0, 0
    for scenario in CONFIG["scenarios"]:
        shifted = centers + scenario["translation_ras_mm"]
        tree = cKDTree(shifted)
        obstacle_radius = base_radius + scenario["boundary_inflation_mm"]
        uncertainty = np.linalg.norm(scenario["translation_ras_mm"]) + scenario["boundary_inflation_mm"]
        for trajectory in trajectories:
            start = np.asarray(anchors[trajectory["anchor"]]["point_ras_mm"])
            end = np.asarray(targets[trajectory["target"]]["point_ras_mm"])
            fast = accelerated_clearance(start, end, shifted, tree, obstacle_radius)
            reference = reference_clearance(start, end, shifted, obstacle_radius)
            errors.append(abs(fast - reference))
            classifications = {}
            for radius, maximum_length in itertools.product(CONFIG["radii_mm"], CONFIG["lengths_mm"]):
                key = f"r{radius:g}_l{maximum_length:g}"
                if not contained(start, end, affine, label.shape, radius + uncertainty):
                    status, ref_status, reason = "abstained", "abstained", "capsule_or_uncertainty_outside_fov"
                elif trajectory["length_mm"] > maximum_length:
                    status, ref_status, reason = "blocked", "blocked", "finite_length_exceeded"
                else:
                    status = "reached" if fast > radius + CONFIG["geometry_tolerance_mm"] else "blocked"
                    ref_status = "reached" if reference > radius + CONFIG["geometry_tolerance_mm"] else "blocked"
                    reason = "clear_static_pose" if status == "reached" else "boundary_sphere_collision"
                mismatch = status != ref_status
                mismatches += int(mismatch)
                false_feasible += int(status == "reached" and ref_status == "blocked")
                counts[scenario["name"]][key][status] += 1
                classifications[key] = {"status": status, "reference_status": ref_status, "reason": reason}
            trajectory["scenarios"].append({
                "name": scenario["name"], "capped_clearance_mm": fast,
                "reference_capped_clearance_mm": reference, "grid": classifications,
            })
    return {
        "status": "evaluated", "expert_anatomical_review": "incomplete",
        "model": "airspace_boundary_spheres_and_finite_capsules_not_surgical_routes",
        "labels": label_records, "targets": targets, "anchors": anchors,
        "boundary_sphere_count": len(centers), "base_boundary_radius_mm": base_radius,
        "trajectories": trajectories, "skipped_segments": skipped, "counts": counts,
        "geometry_comparison": {
            "max_capped_clearance_absolute_error_mm": max(errors),
            "classification_mismatches": mismatches, "false_feasible": false_feasible,
            "scenario_segment_count": len(errors),
            "grid_classification_count": len(errors) * 9,
        },
        "conservative_coverage": {
            scenario: {key: values["reached"] / sum(values.values()) for key, values in grid.items()}
            for scenario, grid in counts.items()
        },
    }
