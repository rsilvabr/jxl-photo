"""#675 — the encoder writes a master ONCE after cjxl.

Each exiftool write command rewrote the whole master (48 MB copied to append
8 KB of metadata), then reorder_jxl_boxes rewrote it to move the metadata in
front of the codestream, then _write_thumbnail_box rewrote it for the Explorer
thumbnail: 7 writes, 337 MB for a 48 MB file. The writes now run on a stub
(cjxl's boxes, the codestream cut to its first bytes) and the master is put
together in one pass. Anything the stub route cannot do falls back to the old
steps on the untouched file. The master is the same, byte for byte.

Real codecs, in a folder named with Japanese, brackets, a space and `100%off %d`.
"""
import datetime as dt
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_photo_common as common
import jxl_tiff_encoder as enc
from _icc_fixtures import romm_toe_icc

HOSTILE = "吾妻山 [組立] 100%off %d"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


@pytest.fixture
def frozen(monkeypatch):
    fixed = dt.datetime(2026, 11, 14, 12, 0, 0)

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.replace(tzinfo=dt.timezone.utc).astimezone(tz)

    monkeypatch.setattr(common, "datetime", Frozen)


def _tiffs(folder):
    folder.mkdir(parents=True)
    rng = np.random.default_rng(6)
    y, x = np.mgrid[0:500, 0:800]
    rgb = np.stack([x * 80, y * 120, np.full(x.shape, 20000)], -1)
    rgb = np.clip(rgb + rng.integers(-3000, 3000, rgb.shape), 0, 65535).astype(np.uint16)
    xmp = (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
           b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
           b'rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title><rdf:Alt>'
           b'<rdf:li xml:lang="x-default">\xe5\x86\x99\xe7\x9c\x9f</rdf:li></rdf:Alt></dc:title>'
           b'</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
    tifffile.imwrite(folder / "写真 (1).tif", rgb, photometric="rgb",
                     resolution=(300, 300), resolutionunit=2,
                     extratags=[(34675, "B", len(romm_toe_icc()), romm_toe_icc(), True),
                                (700, "B", len(xmp), xmp, True)])
    tifffile.imwrite(folder / "灰 [2].tif", rgb[..., 0], photometric="minisblack")


def _run_main(argv):
    old = sys.argv
    sys.argv = ["jxl_tiff_encoder"] + [str(a) for a in argv]
    try:
        try:
            enc.main()
        except SystemExit as e:
            assert e.code in (0, None), f"encoder exited {e.code}"
    finally:
        sys.argv = old


@needs_tools
@pytest.mark.parametrize("thumb", ["on", "off"])
@pytest.mark.parametrize("distance", ["0", "0.5"])
def test_one_pass_master_is_the_old_master(tmp_path, monkeypatch, frozen, thumb, distance):
    src = tmp_path / HOSTILE / "tiff"
    _tiffs(src)
    args = ["--mode", "2", "--distance", distance, "--effort", "1", "--no-preflight",
            "--workers", "2", "--explorer-thumbnail", thumb]
    rewrites = []
    monkeypatch.setattr(enc, "reorder_jxl_boxes", lambda p: rewrites.append(p))
    monkeypatch.setattr(enc, "_write_thumbnail_box", lambda *a, **k: rewrites.append(a))
    _run_main([src, tmp_path / HOSTILE / "new"] + args)
    assert rewrites == [], f"the stub route rewrote the master: {rewrites}"
    monkeypatch.undo()
    frozen_again = dt.datetime(2026, 11, 14, 12, 0, 0)

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_again if tz is None else frozen_again.replace(
                tzinfo=dt.timezone.utc).astimezone(tz)

    monkeypatch.setattr(common, "datetime", Frozen)
    # The old steps, on the same TIFFs: the stub cannot be made.
    monkeypatch.setattr(enc, "_write_metadata_stub",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no stub")))
    _run_main([src, tmp_path / HOSTILE / "old"] + args)
    new = sorted((tmp_path / HOSTILE / "new").glob("*.jxl"))
    old = sorted((tmp_path / HOSTILE / "old").glob("*.jxl"))
    assert [p.name for p in new] == [p.name for p in old] and len(new) == 2
    for a, b in zip(new, old):
        assert a.read_bytes() == b.read_bytes(), a.name
        assert enc._verify_jxl_integrity(a)
    if thumb == "on" and distance == "0":
        # (A small lossy file gets none: its thumbnail would be more than
        # EXPLORER_THUMBNAIL_MAX_SHARE of it — in both routes.)
        fp = enc._codestream_fingerprint(new[0])
        assert fp and enc._thumbnail_box_fingerprint(new[0]) == fp


@needs_tools
def test_a_failing_stub_route_leaves_a_good_master(tmp_path, monkeypatch, frozen):
    """The assembly raising before its replace: the old steps run on the
    untouched file and the master is right."""
    src = tmp_path / HOSTILE / "tiff"
    _tiffs(src)
    monkeypatch.setattr(enc, "_assemble_from_stub",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disagree")))
    _run_main([src, tmp_path / HOSTILE / "out", "--mode", "2", "--distance", "0",
               "--effort", "1", "--no-preflight", "--explorer-thumbnail", "on"])
    outs = sorted((tmp_path / HOSTILE / "out").glob("*.jxl"))
    assert len(outs) == 2 and all(enc._verify_jxl_integrity(p) for p in outs)
    fp = enc._codestream_fingerprint(outs[0])
    assert fp and enc._thumbnail_box_fingerprint(outs[0]) == fp


def test_retarget_copies_the_argfile_and_refuses_an_ambiguous_command(tmp_path):
    target = tmp_path / HOSTILE / "写真 (1).jxl"
    stub = tmp_path / "stub.jxl"
    af = tmp_path / "inject.args"
    af.write_bytes(b"-charset\r\nfilename=utf8\r\n-XMP-dc:Title=x\r\n"
                   + str(target).encode("utf-8") + b"\r\n")
    writes = [(["-overwrite_original", "-a=1", str(target)], "m1", 200),
              (af, "m2", 300)]
    out = enc._retarget_exiftool_writes(writes, target, stub)
    assert out[0][0] == ["-overwrite_original", "-a=1", str(stub)]
    copy = out[1][0]
    assert copy != af and af.read_bytes().count(str(target).encode("utf-8")) == 1
    assert copy.read_bytes() == af.read_bytes().replace(str(target).encode("utf-8"),
                                                        str(stub).encode("utf-8"))
    # A command that names the file twice, or not at all: no stub route.
    assert enc._retarget_exiftool_writes(
        [(["-tagsfromfile", str(target), str(target)], "m", 1)], target, stub) is None
    assert enc._retarget_exiftool_writes([(["-a=1", "other.jxl"], "m", 1)], target, stub) is None


def test_stub_refuses_what_it_cannot_carry(tmp_path):
    bare = tmp_path / "bare.jxl"
    bare.write_bytes(b"\xff\x0a" + b"\x00" * 100)
    with pytest.raises(RuntimeError, match="bare"):
        enc._write_metadata_stub(bare, tmp_path / "s.jxl")
    cut = tmp_path / "cut.jxl"
    cut.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00\x00\x00\x40jxlc" + b"\x00" * 10)
    with pytest.raises(RuntimeError):
        enc._write_metadata_stub(cut, tmp_path / "s2.jxl")
