#!/usr/bin/env python3
"""Bounded voxel-cell safety evidence, not anatomical or surgical validation.

PYTHONPATH=src python research/run_voxel_refinement.py --public-cases 3
No downloads; consumes the existing frozen public archive, if requested.
Existing output is never overwritten. Full-cohort evidence remains untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import tempfile
import time
import zipfile

import numpy as np
from scipy import ndimage
from scipy.optimize import lsq_linear

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from corridorkit.domain.models import VoxelGeometry
from corridorkit.geometry.voxel import VoxelMaskBackend

SEED = 20261001
TOLERANCE_MM = 1e-8


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def independent_distance_bounds(start, end, indices, affine):
    """Convex box least squares + independent support-function lower bound.

    No triangle, slab, KD-tree, or centerline sampling code is reused. For each
    cell minimize ||start + t*(end-start) - (center+A*u)||, 0<=t<=1,
    -.5<=u<=.5. Any feasible residual gives an upper distance bound; projecting
    every feasible residual on its unit direction gives a lower bound. Their
    gap measures optimizer uncertainty; optimizer success alone is not proof.
    """
    matrix = np.column_stack((end - start, -affine[:3, :3]))
    low, high = np.array([0, -.5, -.5, -.5]), np.array([1, .5, .5, .5])
    normals = np.linalg.inv(affine[:3, :3])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    lower_bounds, upper_bounds = [], []
    for index in indices:
        center = affine[:3, :3] @ index + affine[:3, 3]
        offset = start - center
        fit = lsq_linear(matrix, -offset, bounds=(low, high), method="bvls",
                         tol=1e-13, max_iter=1000)
        x = np.clip(fit.x, low, high)
        residual = offset + matrix @ x
        upper = float(np.linalg.norm(residual))
        # Near-zero residuals can have poorly determined directions even when
        # the optimizer's objective is excellent. Independently known affine
        # face normals also yield valid separating support bounds.
        directions = [*normals, *(-normals)]
        if upper:
            directions.append(residual / upper)
        lower = 0.
        for direction in directions:
            coefficients = direction @ matrix
            lower = max(lower, float(
                direction @ offset
                + np.minimum(coefficients * low, coefficients * high).sum()))
        # Explicit roundoff allowance; not an interval-arithmetic certificate.
        # Small local least-squares operations and physical-coordinate
        # subtraction have separate error scales. Using the entire global
        # translation for every optimizer operation needlessly widens bounds.
        eps = np.finfo(float).eps
        guard = (128 * eps * max(1., np.linalg.norm(offset), np.linalg.norm(matrix))
                 + 8 * eps * max(np.linalg.norm(start), np.linalg.norm(center)))
        lower_bounds.append(max(0., lower - guard))
        upper_bounds.append(upper + guard)
    return min(lower_bounds), min(upper_bounds)


def make_backends(path, mask, affine, budget=128):
    np.save(path, mask)
    geometry = VoxelGeometry(uri=str(path), affine=affine.tolist(),
                             max_refinement_cells=budget)
    return (VoxelMaskBackend(geometry),
            VoxelMaskBackend(geometry.model_copy(update={"refine_near_boundary": True})))


def query_row(coarse, refined, start, end, radius):
    start, end = np.asarray(start), np.asarray(end)
    first = coarse.capsule_clearance(start, end, radius)
    second = refined.capsule_clearance(start, end, radius)
    row = {"start_mm": start.tolist(), "end_mm": end.tolist(), "radius_mm": radius,
           "coarse": asdict(first), "optional": asdict(second)}
    # Infinity represents the explicitly empty mask, not known outside-FOV space.
    for result in (row["coarse"], row["optional"]):
        if result["clearance_mm"] is not None and not np.isfinite(result["clearance_mm"]):
            result["clearance_mm"] = "infinity"
    row["recovered_clear"] = bool(first.clearance_mm is not None
                                   and first.clearance_mm <= 0
                                   and second.clearance_mm > 0)
    row["fov_policy_unchanged"] = first.out_of_fov == second.out_of_fov
    row["budget_fallback_identical"] = bool(
        not second.refinement_budget_exceeded or first.clearance_mm == second.clearance_mm)
    return row


def synthetic(output, temporary, draws):
    rng = np.random.default_rng(SEED)
    rows, geometries = [], []
    for family, linear in [
        ("isotropic", np.eye(3)),
        ("anisotropic", np.diag([.2, 1.7, 3.])),
        ("sheared_reflected", np.array([[-.7, 1.1, .3], [0, 1.8, -.4], [0, 0, .35]])),
        ("oblique", np.linalg.qr(rng.normal(size=(3, 3)))[0] @ np.diag([.4, 1.2, 2.2])),
        ("translated_shear", np.array([[1., .8, .2], [.1, 1.1, .3], [0, .2, .6]])),
    ]:
        affine = np.eye(4)
        affine[:3, :3] = linear
        affine[:3, 3] = [1e5, -2e5, 3e5] if family == "translated_shear" else [17, -23, 31]
        indices = np.array([[5, 5, 5], [8, 8, 8], [2, 8, 2]])
        mask = np.zeros((11, 11, 11), dtype=np.uint8)
        mask[tuple(indices.T)] = 1
        coarse, refined = make_backends(temporary / "synthetic.npy", mask, affine)
        geometries.append({"family": family, "affine": affine.tolist(),
                           "indices": indices.tolist(), "shape": list(mask.shape)})
        cases = []
        # Exact face tangencies and +/- gaps, including zero-length centerlines.
        for axis in range(3):
            for sign in (-1, 1):
                normal = sign * np.linalg.inv(linear).T[:, axis]
                normal /= np.linalg.norm(normal)
                face_index = indices[0].astype(float)
                face_index[axis] += sign * .5
                face = linear @ face_index + affine[:3, 3]
                for gap in (-.02, 0., 1e-6, .04, .15):
                    radius = .1
                    point = face + normal * (radius + gap)
                    cases.append(("analytic_face", point, point, radius, gap))
        # Closed-cell corners/edges, endpoint touching, interior segments, and
        # zero-radius near-parallel traversal (no manufactured signed oracle).
        for kind, endpoints in [
            ("corner_touch", [[4., 4., 4.], [4.5, 4.5, 4.5]]),
            ("edge_touch", [[4., 4.5, 4.5], [6., 4.5, 4.5]]),
            ("interior", [[5., 5., 5.], [5.1, 5.1, 5.1]]),
            ("near_parallel", [[3., 5.5 + 1e-8, 5.], [7., 5.5 + 2e-8, 5.]]),
            ("crossing", [[3., 5., 5.], [7., 5., 5.]]),
        ]:
            start, end = np.asarray(endpoints) @ linear.T + affine[:3, 3]
            cases.append((kind, start, end, 0., None))
        for _ in range(draws):
            endpoints = rng.uniform(3., 7., (2, 3)) @ linear.T + affine[:3, 3]
            cases.append(("random_segment", *endpoints, float(rng.uniform(0, .45)), None))
        for kind, start, end, radius, gap in cases:
            row = query_row(coarse, refined, start, end, radius)
            lower, upper = independent_distance_bounds(start, end, indices, affine)
            row.update(family=family, kind=kind, analytic_gap_mm=gap,
                       oracle_lower_mm=lower, oracle_upper_mm=upper,
                       oracle_gap_mm=upper - lower)
            clearance = row["optional"]["clearance_mm"]
            row["clearance_bound_violation"] = bool(
                clearance is not None and clearance > upper - radius + TOLERANCE_MM)
            row["false_clear"] = bool(
                clearance is not None and clearance > 0 and
                (upper < radius or (gap is not None and gap <= 0)))
            row["positive_lower_bound_unresolved"] = bool(
                clearance is not None and clearance > 0 and lower <= radius)
            row["analytic_distance_error_mm"] = (
                None if gap is None else abs((upper + lower) / 2 - radius - gap))
            rows.append(row)
        # Resource/FOV fallback is an acceptance condition, not a missing row.
        limited = VoxelMaskBackend(VoxelGeometry(
            uri=str(temporary / "synthetic.npy"), affine=affine.tolist(),
            refine_near_boundary=True, max_refinement_cells=1))
        start, end = np.array([[3., 3., 3.], [9., 9., 9.]]) @ linear.T + affine[:3, 3]
        row = query_row(coarse, limited, start, end, .1)
        row.update(family=family, kind="budget")
        rows.append(row)
        for axis in range(3):
            for side in (-1, 1):
                index = np.full(3, 5.)
                index[axis] = -.6 if side < 0 else 10.6
                point = linear @ index + affine[:3, 3]
                row = query_row(coarse, refined, point, point, .1)
                row.update(family=family, kind="fov")
                rows.append(row)
    write_json(output / "synthetic-geometries.json", geometries)
    return rows


def public_cases(output, temporary, count, all_eligible=False):
    from corridorkit.data.audit import _load
    source = ROOT / "research/results/airspace-v1"
    manifest = json.loads((source / "frozen-manifest.json").read_text())
    summary = json.loads((source / "summary.json").read_text())
    archive = ROOT / "research/data-cache/NasalSeg-v2.zip"
    if sha256(archive) != manifest["source"]["sha256"]:
        raise ValueError("Archive does not match frozen manifest")
    if sha256(source / "per-case.jsonl.gz") != summary["per_case_records_sha256"]:
        raise ValueError("Upstream records changed")
    with gzip.open(source / "per-case.jsonl.gz", "rt") as stream:
        records = [json.loads(line) for line in stream]
    selected = sorted((r for r in records if r["status"] == "evaluated"
                       and (all_eligible or manifest["subject_split"][r["case_id"]] == "development")),
                      key=lambda r: r["case_id"])[:count]
    if all_eligible and len(selected) != 118:
        raise ValueError(f"Expected all 118 eligible records, got {len(selected)}")
    write_json(output / "public-selection.json", {
        "rule": ("All 118 integrity-eligible IDs; frozen method, no outcome selection or tuning"
                 if all_eligible else
                 "First N lexicographic integrity-eligible development IDs; no outcome selection"),
        "selected": [r["case_id"] for r in selected],
        "source_archive_sha256": sha256(archive),
        "upstream_manifest_sha256": sha256(source / "frozen-manifest.json"),
        "upstream_records_sha256": sha256(source / "per-case.jsonl.gz"),
        "queries": "First 4 frozen targets x first 2 anchors x radii 0/1 mm; "
                   "12 evenly spaced near-boundary air centers x radii 0/.25 mm, "
                   "point capsules. No altered cohort run or clinical labels.",
    })
    rows = []
    with zipfile.ZipFile(archive) as zipped:
        for record in selected:
            identifier = record["case_id"]
            path = temporary / "public-label.nrrd"
            path.write_bytes(zipped.read(manifest["label_members"][identifier]))
            labels, affine = _load(path)
            air = labels > 0
            boundary = ndimage.binary_dilation(air) & ~air
            coarse, refined = make_backends(temporary / "boundary.npy", boundary, affine)
            detail = record.get("analysis", record.get("benchmark", record))
            if "targets" not in detail:
                detail = next(v for v in record.values()
                              if isinstance(v, dict) and "targets" in v)
            queries = []
            for anchor in detail["anchors"][:2]:
                for target in detail["targets"][:4]:
                    for radius in (0., 1.):
                        queries.append(("frozen_anchor_target", anchor["point_ras_mm"],
                                        target["point_ras_mm"], radius))
            near = np.argwhere(air & ndimage.binary_dilation(boundary))
            for index in np.linspace(0, len(near) - 1, min(12, len(near)), dtype=int):
                point = affine[:3, :3] @ near[index] + affine[:3, 3]
                for radius in (0., .25):
                    queries.append(("near_boundary_point", point, point, radius))
            for kind, start, end, radius in queries:
                row = query_row(coarse, refined, start, end, radius)
                row.update(case_id=identifier, kind=kind)
                # Public oracle is bounded to isolated refined queries. Verify
                # all cells in the proven broad-phase ball via a separate
                # box optimizer; remote cells can only cap at .1 mm. This is
                # not an independent test of public broad-phase completeness.
                if row["optional"]["refined"]:
                    start, end = np.asarray(start), np.asarray(end)
                    distance = np.linalg.norm(end - start)
                    candidates = refined.tree.query_ball_point(
                        (start + end) / 2,
                        distance / 2 + refined.voxel_bound_radius_mm + radius + .100001)
                    lower, upper = independent_distance_bounds(
                        start, end, refined.cell_indices[candidates], affine)
                    value = row["optional"]["clearance_mm"]
                    row.update(oracle_cells=len(candidates), oracle_lower_mm=lower,
                               oracle_upper_mm=upper, oracle_gap_mm=upper - lower,
                               false_clear=bool(value > 0 and upper < radius),
                               positive_lower_bound_unresolved=bool(value > 0 and lower <= radius),
                               clearance_bound_violation=bool(
                                   value > upper - radius + TOLERANCE_MM))
                rows.append(row)
            print(f"{identifier}: {len(queries)} bounded public queries", flush=True)
    return rows


def summarize(rows):
    return {
        "queries": len(rows),
        "kinds": dict(Counter(r["kind"] for r in rows)),
        "refined": sum(r["optional"]["refined"] for r in rows),
        "budget_exceeded": sum(r["optional"]["refinement_budget_exceeded"] for r in rows),
        "out_of_fov": sum(r["optional"]["out_of_fov"] for r in rows),
        "recovered_clear": sum(r["recovered_clear"] for r in rows),
        "false_clear": sum(r.get("false_clear", False) for r in rows),
        "clearance_bound_violations": sum(r.get("clearance_bound_violation", False) for r in rows),
        "positive_lower_bound_unresolved": sum(
            r.get("positive_lower_bound_unresolved", False) for r in rows),
        "fov_policy_changes": sum(not r["fov_policy_unchanged"] for r in rows),
        "budget_fallback_changes": sum(not r["budget_fallback_identical"] for r in rows),
        "oracle_queries": sum("oracle_gap_mm" in r for r in rows),
        "max_oracle_gap_mm": max((r.get("oracle_gap_mm", 0) for r in rows), default=0),
        "max_analytic_distance_error_mm": max(
            (r.get("analytic_distance_error_mm") or 0 for r in rows), default=0),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/voxel-refinement-v5")
    parser.add_argument("--random-per-family", type=int, default=64)
    parser.add_argument("--public-cases", type=int, default=0)
    parser.add_argument("--all-eligible", action="store_true",
                        help="Apply the frozen query protocol to all 118 eligible cases, without tuning")
    args = parser.parse_args()
    if args.all_eligible:
        args.public_cases = 118
    if args.random_per_family < 1 or not 0 <= args.public_cases <= (118 if args.all_eligible else 3):
        parser.error("Use positive random count and 0..3 public cases")
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), ROOT / "src/corridorkit/geometry/voxel.py",
               ROOT / "src/corridorkit/geometry/mesh_reference.py",
               ROOT / "src/corridorkit/domain/models.py",
               ROOT / "tests/test_voxel_refinement.py",
               ROOT / "tests/test_voxel_refinement_oracle.py"]
    for path in sources:
        frozen = args.output / "frozen_sources" / path.relative_to(ROOT)
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_bytes(path.read_bytes())
    manifest = {
        "seed": SEED, "random_per_family": args.random_per_family,
        "public_case_limit": args.public_cases, "tolerance_mm": TOLERANCE_MM,
        "all_integrity_eligible_cases": args.all_eligible,
        "acceptance": "zero false-clear, bound violations, unresolved positive lower bounds, "
                      "FOV policy changes, or changed budget fallbacks; oracle gap <= 1e-8 mm",
        "source_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in sources},
        "python": sys.version, "platform": platform.platform(),
        "packages": {p: importlib.metadata.version(p)
                     for p in ("numpy", "scipy", "nibabel", "SimpleITK", "pydantic")},
        "scope": "Closed affine voxel-cell computational model only; no surgical validity",
        "frozen_before_queries": True,
    }
    write_json(args.output / "frozen-manifest.json", manifest)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="voxel-refinement-") as directory:
        temporary = Path(directory)
        synthetic_rows = synthetic(args.output, temporary, args.random_per_family)
        public_rows = public_cases(args.output, temporary, args.public_cases,
                                   args.all_eligible) if args.public_cases else []
    for name, rows in (("synthetic", synthetic_rows), ("public", public_rows)):
        with (args.output / f"{name}-queries.jsonl").open("w") as stream:
            for row in rows:
                stream.write(json.dumps(row, allow_nan=False) + "\n")
    result = {"synthetic": summarize(synthetic_rows), "public": summarize(public_rows),
              "runtime_seconds": time.perf_counter() - started,
              "record_sha256": {name: sha256(args.output / f"{name}-queries.jsonl")
                                for name in ("synthetic", "public")}}
    gate_keys = ("false_clear", "clearance_bound_violations", "positive_lower_bound_unresolved",
                 "fov_policy_changes", "budget_fallback_changes")
    result["passed"] = all(
        all(result[name][key] == 0 for key in gate_keys)
        and result[name]["max_oracle_gap_mm"] <= TOLERANCE_MM
        for name in ("synthetic", "public"))
    write_json(args.output / "summary.json", result)
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
