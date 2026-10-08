"""Offline API/header/cache tests: no network or full atlas needed."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "research" / "prepare_skullbase_atlas.py"
SPEC = importlib.util.spec_from_file_location("prepare_skullbase_atlas", SCRIPT)
atlas = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(atlas)


class Response:
    status_code = 200

    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.payload


class Session:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        return self.responses[url]


def entry(name, payload=b"abc"):
    return {"name": name, "bundleName": "ORIGINAL", "sizeBytes": len(payload),
            "checkSum": {"checkSumAlgorithm": "MD5",
                         "value": hashlib.md5(payload).hexdigest()},
            "_links": {"content": {"href": atlas.ITEM_API + "/fixture-" + name.split(".")[-1]}}}


def test_selection_excludes_large_nii_and_uses_link():
    originals = [entry(n) for n in atlas.WANTED]
    other = entry("CT_template_pub.nii")
    other["sizeBytes"] = 200_376_480
    assert atlas.select_originals(originals + [other]) == originals
    assert atlas.select_originals(originals)[0]["_links"]["content"]["href"].endswith(
        "/fixture-nrrd")


@pytest.mark.parametrize("failure", ["duplicate", "missing", "large", "total", "not-original"])
def test_selection_fails_closed(failure):
    originals = [entry(n) for n in atlas.WANTED]
    if failure == "duplicate":
        originals.append(originals[0])
    elif failure == "missing":
        originals.pop()
    elif failure == "large":
        originals[0]["sizeBytes"] = atlas.MAX_BYTES
    elif failure == "total":
        for original in originals:
            original["sizeBytes"] = atlas.MAX_BYTES // 2
    else:
        originals[0]["bundleName"] = "THUMBNAIL"
    with pytest.raises(ValueError):
        atlas.select_originals(originals)


def test_paginated_api_follows_actual_links():
    first, second = atlas.ITEM_API + "/a", atlas.ITEM_API + "/b"
    session = Session({
        first: Response(json.dumps({"_embedded": {"bundles": [{"name": "one"}]},
                                    "_links": {"next": {"href": second}}}).encode()),
        second: Response(json.dumps({"_embedded": {"bundles": [{"name": "two"}]}}).encode()),
    })
    assert atlas.collection(session, first, "bundles") == [{"name": "one"}, {"name": "two"}]
    assert session.urls == [first, second]


@pytest.mark.parametrize("url", ["http://digital.lib.washington.edu/server/api/x",
                                "https://example.com/server/api/x",
                                "https://digital.lib.washington.edu/guessed-download"])
def test_untrusted_urls_rejected(url):
    with pytest.raises(ValueError):
        atlas.trusted_url(url)


def test_download_hash_and_cache_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(atlas.shutil, "disk_usage",
                        lambda _: type("Disk", (), {"free": 10**9})())
    original = entry(atlas.CT_NAME)
    url = original["_links"]["content"]["href"]
    session = Session({url: Response(b"abc", {"Content-Length": "3"})})
    result = atlas.download(session, original, tmp_path)
    assert result["sha256"] == hashlib.sha256(b"abc").hexdigest()
    result = atlas.download(session, original, tmp_path)
    assert result["reused_verified_cache"]
    assert len(session.urls) == 1
    (tmp_path / atlas.CT_NAME).write_bytes(b"bad")
    with pytest.raises(ValueError, match="checksum"):
        atlas.download(session, original, tmp_path)
    assert (tmp_path / atlas.CT_NAME).read_bytes() == b"bad"


@pytest.mark.parametrize("payload", [b"a", b"abcd", b"bad"])
def test_download_failure_cleans_partial(tmp_path, payload, monkeypatch):
    monkeypatch.setattr(atlas.shutil, "disk_usage",
                        lambda _: type("Disk", (), {"free": 10**9})())
    original = entry(atlas.CT_NAME)
    session = Session({original["_links"]["content"]["href"]: Response(payload)})
    with pytest.raises(ValueError):
        atlas.download(session, original, tmp_path)
    assert not list(tmp_path.iterdir())


def test_download_rejects_insufficient_disk_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(atlas.shutil, "disk_usage",
                        lambda _: type("Disk", (), {"free": 1})())
    session = Session({})
    with pytest.raises(ValueError, match="disk headroom"):
        atlas.download(session, entry(atlas.CT_NAME), tmp_path)
    assert session.urls == []
    assert not list(tmp_path.iterdir())


def test_download_refuses_symlink(tmp_path):
    (tmp_path / atlas.CT_NAME).symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        atlas.download(Session({}), entry(atlas.CT_NAME), tmp_path)


def test_layered_nrrd_preserves_overlapping_segments_and_signed_geometry(tmp_path):
    path = tmp_path / "fixture.seg.nrrd"
    path.write_bytes(
        b"NRRD0004\n"
        b"type: unsigned char\n"
        b"dimension: 4\n"
        b"space: left-posterior-superior\n"
        b"sizes: 2 3 4 5\n"
        b"space directions: none (-1,0,0) (0,2,0) (0,0,3)\n"
        b"space origin: (10,20,30)\n"
        b"Segment0_Name:=A\nSegment0_Layer:=0\nSegment0_LabelValue:=1\n"
        b"Segment0_Extent:=0 2 0 3 0 4\n"
        b"Segment1_Name:=B\nSegment1_Layer:=1\nSegment1_LabelValue:=1\n\n"
        b"\xff\x00")
    result = atlas.inspect_header(atlas.read_header(path))
    assert result["sizes_nrrd_axis_order"] == [2, 3, 4, 5]
    assert [s["Layer"] for s in result["segments"]] == ["0", "1"]
    assert [s["LabelValue"] for s in result["segments"]] == ["1", "1"]
    bounds = result["voxel_center_bounds_native_mm"]
    np.testing.assert_allclose(bounds["min"], [8, 20, 30])
    np.testing.assert_allclose(bounds["max"], [10, 26, 42])
    assert result["voxel_edge_bounds_native_mm"]["min"] == [7.5, 19, 28.5]


def test_invalid_header_rejected(tmp_path):
    path = tmp_path / "broken.nrrd"
    path.write_bytes(b"NRRD0004\nsizes: 3 4 5\n")
    with pytest.raises(ValueError, match="Missing"):
        atlas.read_header(path)


def test_legacy_payload_keeps_overlaps(tmp_path):
    sitk = pytest.importorskip("SimpleITK")
    data = np.zeros((3, 4, 5, 2), dtype=np.uint8)
    data[1, 2, 3, :] = 1
    sitk.WriteImage(sitk.GetImageFromArray(data, isVector=True), str(tmp_path / atlas.SEG_NAME))
    inspection = {atlas.SEG_NAME: {"segments": [
        {"Name": "one", "Extent": "3 3 2 2 1 1"},
        {"Name": "two", "Extent": "3 3 2 2 1 1"},
    ]}}
    original = (tmp_path / atlas.SEG_NAME).read_bytes()
    result = atlas.inspect_payload(tmp_path, inspection, False)
    assert result["overlap_voxels"] == 1
    assert [s["nonzero_voxels"] for s in result["segment_counts"]] == [1, 1]
    assert all(s["header_extent_matches"] for s in result["segment_counts"])
    assert (tmp_path / atlas.SEG_NAME).read_bytes() == original


def test_discovery_follows_bundle_and_content_links(monkeypatch):
    bundles_url = atlas.ITEM_API + "/returned-bundles"
    streams_url = atlas.ITEM_API + "/returned-streams"
    item = {"handle": "1773/46259", "_links": {"bundles": {"href": bundles_url}}}
    originals = [entry(name) for name in atlas.WANTED]
    session = Session({
        atlas.ITEM_API: Response(json.dumps(item).encode()),
        bundles_url: Response(json.dumps({"_embedded": {"bundles": [
            {"name": "ORIGINAL", "_links": {"bitstreams": {"href": streams_url}}},
            {"name": "LICENSE", "_links": {}},
        ]}}).encode()),
        streams_url: Response(json.dumps({"_embedded": {"bitstreams": originals}}).encode()),
    })
    assert atlas.discover(session) == (item, originals)
    assert session.urls == [atlas.ITEM_API, bundles_url, streams_url]


def test_insufficient_disk_refuses_network(tmp_path, monkeypatch):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(atlas.shutil, "disk_usage", lambda _: usage(100, 99, 1))
    session = Session({})
    with pytest.raises(ValueError, match="headroom"):
        atlas.download(session, entry(atlas.CT_NAME), tmp_path)
    assert session.urls == []
