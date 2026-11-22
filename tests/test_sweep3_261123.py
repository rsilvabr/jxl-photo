"""The bugs of Fable's sweep of v2.31.0-rc7 by sibling rules (2026-11-23,
AI_tools/261123_Fable_review_rc7-sweep.md): a proof one script requires and its
sibling does not. One test each; real codecs where the bug is in what they read
or write; every folder is hostile (Japanese, [ ], a space; '%' where ExifTool or
ImageMagick read the path).
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo_common as common  # noqa: E402,F401
import jxl_recompressor as rec  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "250118_吾妻山公園 [FINAL] x"
PERCENT = "100%off %d [x] y"
needs_codecs = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")
needs_magick = pytest.mark.skipif(not shutil.which("magick"), reason="needs ImageMagick")


def _script(name, *args, env=None):
    e = dict(os.environ, JXLPHOTO_SETTINGS="")
    e.update(env or {})
    return subprocess.run([sys.executable, str(REPO / name), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=900, env=e)


def _main(mod, monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", [mod.__name__ + ".py", *map(str, args)])
    try:
        mod.main()
    except SystemExit as e:
        return e.code or 0
    return 0


def _md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def _adobe_icc() -> bytes:
    return rec._build_matrix_trc_icc("AdobeRGB (test)",
                                     [(0.64, 0.33), (0.21, 0.71), (0.15, 0.06)],
                                     (0.3127, 0.3290), 2.2)


def _tiff_icc(path: Path):
    with tifffile.TiffFile(str(path)) as t:
        tag = t.pages[0].tags.get(34675)
        return bytes(tag.value) if tag is not None else None


# --- L1: the decoder took a FAILED read of the XMP profile for "no profile" ---------

@needs_codecs
def test_l1_a_profile_that_could_not_be_read_is_not_no_profile(tmp_path, monkeypatch):
    """A "skip" master keeps its profile in XMP only. When that read failed (a
    killed ExifTool, a timeout), the decoder wrote the TIFF with NO ICC tag,
    said OK, and --delete-source deleted the JXL, the profile's only copy."""
    import jxl_tiff_decoder as dec
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "DSC_0001 (2).tif"),
                     np.random.default_rng(1).integers(0, 65535, (48, 64, 3), dtype=np.uint16),
                     photometric="rgb", iccprofile=_adobe_icc())
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0.5",
                "--effort", "1", "--workers", "1", "--icc-png-strategy", "skip")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    master = masters / "DSC_0001 (2).jxl"
    calls = []
    real = dec._read_creator_and_relation

    def flaky(p, *a, **k):
        calls.append(p)
        if len(calls) == 1:
            raise RuntimeError("exiftool could not read it (timeout)")
        return real(p, *a, **k)
    monkeypatch.setattr(dec, "_read_creator_and_relation", flaky)
    out = tmp_path / HOSTILE / "TIFF back"
    rc = _main(dec, monkeypatch, masters, out, "--mode", "0", "--workers", "1",
               "--delete-source", "--delete-confirm-off", "--no-preview")
    assert master.exists()
    tif = out / "DSC_0001 (2).tif"
    assert rc != 0 or (tif.exists() and _tiff_icc(tif) == _adobe_icc())


# --- L2: a retouched export proved its master could be deleted ----------------------

@needs_codecs
@needs_magick
def test_l2_a_retouched_png_export_does_not_delete_its_master(tmp_path):
    """The transcoder's exports carry no record of their pixels (the decoder's
    jxlphoto-pixsum): a 16-bit PNG retouched in an editor that keeps the XMP
    still proved its master, and --delete-skipped deleted it."""
    from PIL import Image
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "DSC_0001 (2).tif"),
                     np.random.default_rng(2).integers(0, 65535, (48, 64, 3), dtype=np.uint16),
                     photometric="rgb")
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    master = masters / "DSC_0001 (2).jxl"
    pngs = tmp_path / HOSTILE / "PNG"
    args = [master, pngs, "--mode", "0", "--force-convert", "--decode", "--format", "png",
            "--bit-depth", "16", "--workers", "1"]
    r = _script("jxl_jpeg_transcoder.py", *args)
    png = pngs / "DSC_0001 (2).png"
    assert r.returncode == 0 and png.exists(), (r.stdout + r.stderr)[-3000:]
    # the retouch: other pixels (ImageMagick keeps 16 bits; an ASCII copy, as
    # magick reads [ ] in a name), the export's own XMP (its marks) copied back
    plain = tmp_path / "orig.png"
    shutil.copyfile(png, plain)
    retouch = tmp_path / "retouch.png"
    m = subprocess.run(["magick", str(plain), "-negate", str(retouch)], capture_output=True)
    assert m.returncode == 0, m.stderr
    with Image.open(retouch) as a, Image.open(plain) as b:
        assert a.tobytes() != b.tobytes()
    r2 = rec._run_exiftool_argfile(["-overwrite_original", "-tagsfromfile", str(png), "-xmp",
                                    str(retouch)], timeout=60)
    assert r2.returncode == 0, r2.stderr
    shutil.copyfile(retouch, png)
    os.utime(png, (master.stat().st_mtime + 60,) * 2)       # newer than the master: a skip
    r = _script("jxl_jpeg_transcoder.py", *args, "--delete-source", "--delete-skipped",
                "--delete-confirm-off")
    out = " ".join((r.stdout + r.stderr).split())
    assert master.exists(), out[-3000:]


# --- L3: a lossy JPEG -> JXL deleted a Motion Photo's video with its JPEG -----------

def _jpeg(path: Path, seed=3):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:96, 0:128]
    arr = np.stack([(x * 2 + seed) % 256, (y * 2) % 256, ((x + y) + seed) % 256],
                   axis=2).astype(np.uint8)
    Image.fromarray(arr).save(str(path), quality=90)
    return path


@needs_codecs
def test_l3_a_motion_photo_is_kept_by_a_lossy_conversion(tmp_path):
    """cjxl encodes a JPEG's first picture only. #641 kept a JPEG with an HDR
    gain map; a Motion Photo's video (or any data after the picture that no MPF
    index explains) went with its JPEG, without a word (Fable's L3)."""
    d = tmp_path / HOSTILE / PERCENT
    motion = _jpeg(d / "PXL_20250101_000000000.MP (1).jpg")
    r = rec._run_exiftool_argfile(["-overwrite_original", "-XMP-GCamera:MotionPhoto=1",
                                   "-XMP-GCamera:MotionPhotoVersion=1", str(motion)],
                                  timeout=60)
    assert r.returncode == 0, r.stderr
    with open(motion, "ab") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + os.urandom(30000))
    trailer = _jpeg(d / "other trailer (2).jpg", seed=5)
    with open(trailer, "ab") as f:
        f.write(b"SOMETHINGELSE" + os.urandom(5000))
    plain = _jpeg(d / "plain (3).jpg", seed=7)
    # A camera's preview after the picture without an MPF index (an Olympus
    # writes one): a copy of the picture, like an MPO's, by the owner's call.
    preview = _jpeg(d / "P9230179 (4).jpg", seed=9)
    small = _jpeg(tmp_path / "small.jpg", seed=11)
    with open(preview, "ab") as f:
        f.write(small.read_bytes())
    r = _script("jxl_jpeg_transcoder.py", d, "--mode", "0", "--force-convert", "--distance",
                "1", "--effort", "3", "--workers", "1", "--delete-source",
                "--delete-confirm-off")
    out = " ".join((r.stdout + r.stderr).split())
    assert motion.exists() and trailer.exists(), out[-3000:]
    assert not plain.exists(), out[-3000:]                       # the control
    assert not preview.exists(), out[-3000:]
    assert "a Motion Photo's video" in out, out[-3000:]
    jxl = d / "PXL_20250101_000000000.MP (1).jxl"
    tags = rec._run_exiftool_argfile(["-s", "-XMP-GCamera:all", str(jxl)], timeout=60).stdout
    assert "MotionPhoto" not in tags, tags


# --- L4: a copy out of staging was never compared with the staged file ---------------

def _staging_g(delete):
    import logging
    import types
    return types.SimpleNamespace(DELETE_SOURCE=delete, logger=logging.getLogger("t_l4"),
                                 _abort_if_disk_full=lambda *a, **k: None)


@pytest.mark.parametrize("delete", [True, False], ids=["deleting run", "no delete"])
def test_l4_a_damaged_copy_out_of_staging_is_refused_in_a_deleting_run(tmp_path, monkeypatch,
                                                                       delete):
    """A move to another drive is copy + unlink. After a copy that damaged one
    byte without an error, the final name held a damaged file and the delete
    gates judged the staged one (Fable's L4). In a run that deletes, the copy
    is compared with the staged file first; without a delete, as before."""
    staged = tmp_path / "staging" / "x (1).jpg"
    staged.parent.mkdir()
    payload = bytes(range(256)) * 64
    staged.write_bytes(payload)
    final = tmp_path / HOSTILE / "x (1).jpg"
    final.parent.mkdir(parents=True)

    def damaging_copy(src, dst, *a, **k):
        data = bytearray(Path(src).read_bytes())
        data[len(data) // 2] ^= 0xFF
        Path(dst).write_bytes(bytes(data))
        return dst

    def damaging_move(src, dst, *a, **k):          # what a move across drives is
        damaging_copy(src, dst)
        Path(src).unlink()
        return dst
    # The staging folder on another drive than the output (tmp_path is one
    # volume). Python 3.12 copies with CopyFile2 on Windows (copyfile is not
    # called), so the damage goes in at copy2 — and at move, rc7's route.
    monkeypatch.setattr(common, "_same_volume", lambda a, b: False, raising=False)
    monkeypatch.setattr(shutil, "copy2", damaging_copy)
    monkeypatch.setattr(shutil, "move", damaging_move)
    landed = common._promote_from_staging(_staging_g(delete), staged, final)
    leftovers = [p.name for p in final.parent.iterdir() if p.name.endswith(".tmp")]
    if delete:
        assert landed is False and not final.exists() and staged.exists(), (landed, leftovers)
        assert leftovers == []
    else:
        assert landed is True and final.exists()


# --- L5: a JXL with more than one extra channel ------------------------------------

def _multi_channel_jxl(path: Path):
    """RGB + alpha + a depth channel (other programs write such files)."""
    w, h = 64, 48
    data = np.random.default_rng(1).integers(0, 255, (h, w, 5), dtype=np.uint8)
    pam = path.with_suffix(".pam")
    pam.write_bytes(f"P7\nWIDTH {w}\nHEIGHT {h}\nDEPTH 5\nMAXVAL 255\nTUPLTYPE RGB\n"
                    f"TUPLTYPE Alpha\nTUPLTYPE Depth\nENDHDR\n".encode() + data.tobytes())
    r = rec._run_captured(["cjxl", str(pam), str(path), "-d", "0", "-e", "1"], 300)
    assert r.returncode == 0, r.stderr
    pam.unlink()
    return path


@needs_codecs
@needs_magick
@pytest.mark.skipif(not shutil.which("jxlinfo"), reason="needs jxlinfo")
def test_l5_a_jxl_with_more_extra_channels_is_not_deleted(tmp_path):
    """The decoder's TIFF and the transcoder's 16-bit PNG hold the colour and
    one alpha; a depth channel is left out — and both gates deleted the JXL
    (Fable's L5)."""
    d = tmp_path / HOSTILE
    d.mkdir(parents=True)
    a = _multi_channel_jxl(d / "layers (1).jxl")
    r = _script("jxl_tiff_decoder.py", d, tmp_path / HOSTILE / "TIFF", "--mode", "0",
                "--workers", "1", "--delete-source", "--delete-confirm-off", "--no-preview")
    assert a.exists(), (r.stdout + r.stderr)[-3000:]
    r = _script("jxl_jpeg_transcoder.py", a, tmp_path / HOSTILE / "PNG", "--mode", "0",
                "--force-convert", "--decode", "--format", "png", "--bit-depth", "16",
                "--workers", "1", "--delete-source", "--delete-confirm-off")
    assert a.exists(), (r.stdout + r.stderr)[-3000:]


# --- W1: one Ctrl+C in the recompressor's delete gate cut a multi-page group --------

def _three_page_masters(root: Path) -> Path:
    src = root / "TIFF"
    src.mkdir(parents=True)
    rng = np.random.default_rng(4)
    with tifffile.TiffWriter(str(src / "scan [3p] (1).tif")) as w:
        for _ in range(3):
            w.write(rng.integers(0, 65535, (180, 240, 3), dtype=np.uint16), photometric="rgb")
    masters = root / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert len(list(masters.glob("*.jxl"))) == 3
    return masters


@needs_codecs
def test_w1_a_ctrl_c_in_the_delete_gate_never_cuts_a_group(tmp_path, monkeypatch):
    """The recompressor ran its delete gate after the Ctrl+C block: a press
    during the deletes raised between two unlinks, and the group was cut —
    later runs kept the other pages "for good" (Fable's W1)."""
    import signal
    root = tmp_path / HOSTILE
    masters = _three_page_masters(root)
    real_unlink = Path.unlink
    pressed = []

    def unlink_then_press(self, *a, **k):
        real_unlink(self, *a, **k)
        if self.parent == masters and not pressed:
            pressed.append(self.name)
            signal.raise_signal(signal.SIGINT)
    monkeypatch.setattr(Path, "unlink", unlink_then_press)
    try:
        _main(rec, monkeypatch, masters, root / "small", "--mode", "0", "--distance", "2",
              "--effort", "1", "--workers", "1", "--on-downgrade", "convert",
              "--on-regeneration", "convert", "--on-unknown", "convert", "--delete-source",
              "--delete-confirm-off")
    except KeyboardInterrupt:
        pass
    left = sorted(p.name for p in masters.glob("*.jxl"))
    assert pressed, "the gate deleted nothing"
    assert len(left) in (0, 3), left            # the whole group, or none of it


# --- W2: in place, a group was swapped page by page with nothing to undo ------------

@needs_codecs
@pytest.mark.skipif(os.name != "nt", reason="a file held open blocks a rename on Windows only")
def test_w2_an_in_place_group_is_replaced_whole_or_not_at_all(tmp_path):
    """One page of a 3-page group open in another program: its siblings were
    replaced, its own swap failed — one document in two generations, held so
    by every later run (Fable's W2)."""
    root = tmp_path / HOSTILE
    masters = _three_page_masters(root)
    pages = sorted(masters.glob("*.jxl"))
    before = {p.name: _md5(p) for p in pages}
    with open(pages[-1], "rb"):
        r = _script("jxl_recompressor.py", masters, "--mode", "8", "--distance", "2",
                    "--effort", "1", "--workers", "1", "--no-keep-smaller",
                    "--on-downgrade", "convert", "--on-regeneration", "convert",
                    "--on-unknown", "convert", "--delete-confirm-off")
    out = " ".join((r.stdout + r.stderr).split())
    after = {p.name: _md5(p) for p in sorted(masters.glob("*.jxl"))}
    assert after == before, out[-3000:]                     # none replaced
    assert not list(masters.glob("*.jxlphoto-swap")), out[-3000:]
    assert not list(masters.glob("*.tmp")), out[-3000:]


# --- W3: a failed read of the source's marks, then its delete ------------------------

def _origin_read_fails(monkeypatch):
    """ExifTool answers the origin read with an error (killed, a timeout)."""
    monkeypatch.setattr(common, "_origin_read_args",
                        lambda path: ["-j", "-XMP-dc:Relation", str(path) + ".gone"])


def _relation(path: Path) -> str:
    r = subprocess.run(["exiftool", "-charset", "filename=utf8", "-s3", "-XMP-dc:Relation",
                        str(path)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.stdout


@needs_codecs
def test_w3_encoder_keeps_a_tiff_whose_marks_could_not_be_read(tmp_path, monkeypatch):
    """The master got no jxlphoto-origin (JP-17's class mark), the run said OK
    and deleted the TIFF, the only file that mark can come from (Fable's W3)."""
    import jxl_tiff_encoder as enc
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    tif = src / "DSC_0001 (2).tif"
    tifffile.imwrite(str(tif), np.random.default_rng(2).integers(
        0, 65535, (48, 64, 3), dtype=np.uint16), photometric="rgb")
    out = tmp_path / HOSTILE / "JXL"
    _origin_read_fails(monkeypatch)
    _main(enc, monkeypatch, src, out, "--mode", "0", "--distance", "0", "--effort", "1",
          "--workers", "1", "--delete-source", "--delete-confirm-off")
    jxl = out / "DSC_0001 (2).jxl"
    assert jxl.exists() and "jxlphoto-origin" not in _relation(jxl)
    assert tif.exists()


@needs_codecs
def test_w3_transcoder_keeps_a_jpeg_whose_marks_could_not_be_read(tmp_path, monkeypatch):
    import jxl_jpeg_transcoder as tr
    jpg = _jpeg(tmp_path / HOSTILE / "in" / "IMG_0001 (2).jpg")
    out = tmp_path / HOSTILE / "JXL"
    _origin_read_fails(monkeypatch)
    _main(tr, monkeypatch, jpg.parent, out, "--mode", "0", "--force-convert", "--from-jpeg",
          "--distance", "1", "--effort", "1", "--workers", "1", "--delete-source",
          "--delete-confirm-off")
    assert (out / "IMG_0001 (2).jxl").exists()
    assert jpg.exists()


@needs_codecs
def test_w3_recompressor_keeps_a_master_whose_marks_could_not_be_read(tmp_path, monkeypatch):
    root = tmp_path / HOSTILE
    masters = _three_page_masters(root)
    _origin_read_fails(monkeypatch)
    _main(rec, monkeypatch, masters, root / "small", "--mode", "0", "--distance", "2",
          "--effort", "1", "--workers", "1", "--on-downgrade", "convert",
          "--on-regeneration", "convert", "--on-unknown", "convert", "--delete-source",
          "--delete-confirm-off")
    assert len(list((root / "small").glob("*.jxl"))) == 3
    assert len(list(masters.glob("*.jxl"))) == 3


# --- S1: ImageMagick could not write the temp of a long output path -----------------

@needs_codecs
@needs_magick
def test_s1_a_resized_jpeg_on_a_long_path_is_written(tmp_path):
    """The temp beside the final name is 37 characters longer than it, and
    ImageMagick cannot open a path of 260 or more: every resized, sharpened or
    colour-converted JPEG/PNG whose final path had 223+ characters was an
    error (Fable's S1; the plain djxl route works)."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "DSC_0001 (2).tif"), np.random.default_rng(6).integers(
        0, 65535, (96, 128, 3), dtype=np.uint16), photometric="rgb")
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    name = "DSC_0001 (2).jpg"
    base = tmp_path / HOSTILE / "out"
    pad = 240 - len(str(base)) - len(name) - 2
    out = base / ("長い [p] " + "x" * max(1, pad - 8))
    assert len(str(out / name)) >= 235, len(str(out / name))
    r = _script("jxl_jpeg_transcoder.py", masters, out, "--mode", "0", "--force-convert",
                "--decode", "--format", "jpeg", "--resize-long", "64", "--workers", "1")
    assert r.returncode == 0 and (out / name).exists(), " ".join((r.stdout + r.stderr).split())[-2500:]


# --- S2: a verbatim copy of another program's bare codestream ------------------------

@needs_codecs
@pytest.mark.parametrize("policy", [["--on-unknown", "copy", "--distance", "2"],
                                    ["--on-unknown", "convert", "--distance", "0.2"]],
                         ids=["policy copy", "keep-smaller copy"])
def test_s2_a_bare_codestream_copied_as_it_is_is_not_an_error(tmp_path, policy):
    """The copy was checked with the box walk, which refuses a bare codestream
    by design: "copied file failed the JXL integrity check", an error on every
    run (Fable's S2). It is proven by its bytes now — and deleted on them."""
    from PIL import Image
    src = tmp_path / HOSTILE / "in"
    src.mkdir(parents=True)
    png = tmp_path / "p.png"
    Image.fromarray(np.random.default_rng(7).integers(0, 255, (64, 96, 3), dtype=np.uint8)
                    ).save(str(png))
    bare = src / "other program (1).jxl"
    r = rec._run_captured(["cjxl", str(png), str(bare), "-d", "1", "-e", "1"], 300)
    assert r.returncode == 0 and bare.read_bytes()[:2] == b"\xff\x0a"
    want = _md5(bare)
    out = tmp_path / HOSTILE / "out"
    r = _script("jxl_recompressor.py", src, out, "--mode", "0", *policy, "--effort", "1",
                "--workers", "1", "--delete-source", "--delete-confirm-off")
    text = " ".join((r.stdout + r.stderr).split())
    assert r.returncode == 0, text[-2500:]
    assert _md5(out / bare.name) == want, text[-2500:]
    assert not bare.exists(), text[-2500:]


# --- S3: the wizard charged the token for a run the child refused with exit 2 --------

class _Menu:
    def __init__(self):
        self.errors = []

    def _print_error(self, msg):
        self.errors.append(msg)


def test_s3_a_profile_next_to_a_delete_on_jpeg_to_jxl_is_refused_before_the_token():
    """[D] + Output profile on JPEG -> JXL lossy: the wizard asked the HHMM
    token, the transcoder refused --icc-profile next to --delete-source."""
    import jxl_photo as wp
    wf = {"origin_format": "jpeg", "dest_format": "jxl", "conversion_type": "convert_lossy",
          "mode": 1, "advanced_options": {"delete_source": True}, "expert_flags": "",
          "extra_options": {}, "input_dir": "x", "icc_profile": "sRGB"}
    m = _Menu()
    assert wp._gate_direct_derivatives(m, wf, {}) is True, m.errors
    assert any("ignored on JPEG/PNG" in e for e in m.errors), m.errors


@pytest.mark.parametrize("flags", ["--bit-depth 8", "--format jpeg"])
def test_s3_the_lossy_decode_rule_reads_the_expert_flags(flags):
    """[D] on JXL -> PNG 16-bit with --bit-depth 8 or --format jpeg in the
    expert flags: they come last and win, and the child refused the delete."""
    import jxl_photo as wp
    wf = {"origin_format": "jxl", "dest_format": "png", "conversion_type": "jxl_to_png",
          "bit_depth": 16, "advanced_options": {"delete_source": True},
          "expert_flags": flags}
    assert wp._lossy_decode_never_deletes(wf)
    assert wp._lossy_decode_never_deletes(dict(wf, expert_flags="")) is None


@needs_codecs
def test_s3_a_lone_delete_skipped_is_inert_in_the_childrens_refusals(tmp_path):
    """--delete-skipped alone deletes nothing (each child says so), but the
    derivative refusals tested it next to --delete-source: exit 2."""
    from PIL import Image
    src = tmp_path / HOSTILE / "in"
    src.mkdir(parents=True)
    png = tmp_path / "p.png"
    Image.fromarray(np.zeros((32, 48, 3), dtype=np.uint8)).save(str(png))
    r = rec._run_captured(["cjxl", str(png), str(src / "a (1).jxl"), "-d", "1", "-e", "1"],
                          300)
    assert r.returncode == 0
    for script, extra in (("jxl_jpeg_transcoder.py", ["--force-convert", "--decode",
                                                      "--format", "jpeg"]),
                          ("jxl_recompressor.py", ["--distance", "2"])):
        r = _script(script, src, tmp_path / HOSTILE / script, "--mode", "0", *extra,
                    "--resize-long", "16", "--delete-skipped", "--dry-run", "--workers", "1")
        assert r.returncode != 2, (script, " ".join((r.stdout + r.stderr).split())[-1500:])


# --- S4: a self-hash line that names other bytes made an archive "swapped" for good ---

@needs_codecs
def test_s4_a_stale_self_hash_line_is_settled_by_the_reconstruction(tmp_path):
    """A rewrite whose new self-hash line was never filed (checksums.md5 held
    by another program, a kill between the swap and the line) left the old
    line, and every decode with --delete-skipped refused the archive as
    "swapped" (Fable's S4). The reconstruction decides, and --repair-jbrd
    re-files the line."""
    jpg = _jpeg(tmp_path / HOSTILE / "in" / "IMG_0001 (2).jpg")
    arch = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_jpeg_transcoder.py", jpg.parent, arch, "--mode", "0",
                "--force-transcode", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    jxl = arch / "IMG_0001 (2).jxl"
    db = arch / "checksums.md5"
    with open(db, "a", encoding="utf-8") as f:            # the old line, last
        f.write(f"{'0' * 32}  {jxl.name}.jxl-md5\n")
    r = _script("jxl_jpeg_transcoder.py", arch, "--repair-jbrd", "--workers", "1")
    text = " ".join((r.stdout + r.stderr).split())
    assert "SELF-HASH LINE RE-FILED" in text, text[-2500:]
    lines = [ln for ln in db.read_text(encoding="utf-8").splitlines()
             if ln.endswith(".jxl-md5")]
    assert lines[-1].split()[0] == _md5(jxl), lines
    with open(db, "a", encoding="utf-8") as f:            # stale again, for the gate
        f.write(f"{'0' * 32}  {jxl.name}.jxl-md5\n")
    back = tmp_path / HOSTILE / "back"
    back.mkdir()
    shutil.copy2(jpg, back / jpg.name)                    # the JPEG is already there
    r = _script("jxl_jpeg_transcoder.py", arch, back, "--mode", "0", "--force-transcode",
                "--decode", "--workers", "1", "--delete-source", "--delete-skipped",
                "--delete-confirm-off")
    text = " ".join((r.stdout + r.stderr).split())
    assert r.returncode == 0 and "swapped" not in text, text[-2500:]
    assert not jxl.exists(), text[-2500:]


# --- S5: a custom folder name in the settings hid the user's own files at INFO --------

def test_s5_a_custom_folder_name_that_hides_files_is_a_warning(tmp_path):
    """A decoder set to restore into the folder the exports came from (the
    symmetric round trip) hid every export from the encoder's mode 6, and a
    recompressor JXL_FOLDER_NAME = "JXL" every master in a "JXL" folder; the
    run said it at INFO only (Fable's S5). The skip stays; a WARNING names it."""
    import json
    lib = tmp_path / HOSTILE / "lib"
    tif = lib / "shoot" / "_EXPORT" / "TIFF16" / "DSC_0001 (2).tif"
    tif.parent.mkdir(parents=True)
    tifffile.imwrite(str(tif), np.zeros((16, 16, 3), dtype=np.uint16), photometric="rgb")
    jxl = lib / "2025 event" / "JXL" / "a (1).jxl"
    jxl.parent.mkdir(parents=True)
    jxl.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")
    # This tool's own outputs in a custom-named folder (the common case, the
    # review of the fix): a source beside the folder or in a sibling one, so
    # they are skipped without a warning.
    own_tif = lib / "shoot2" / "_EXPORT" / "TIFF16" / "DSC_0002.tif"
    own_tif.parent.mkdir(parents=True)
    tifffile.imwrite(str(own_tif), np.zeros((16, 16, 3), dtype=np.uint16), photometric="rgb")
    (lib / "shoot2" / "_EXPORT" / "16B_JXL").mkdir()
    (lib / "shoot2" / "_EXPORT" / "16B_JXL" / "DSC_0002.jxl").write_bytes(b"x")
    (lib / "other" / "JXL").mkdir(parents=True)
    (lib / "other" / "b.jxl").write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")
    (lib / "other" / "JXL" / "b.jxl").write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")
    overlay = tmp_path / "overlay.json"
    overlay.write_text(json.dumps({"jxl_tiff_decoder": {"EXPORT_TIFF_FOLDER": "TIFF16"},
                                   "jxl_recompressor": {"JXL_FOLDER_NAME": "JXL"}}),
                       encoding="utf-8")
    env = {"JXLPHOTO_SETTINGS": str(overlay)}
    r = _script("jxl_tiff_encoder.py", lib, "--mode", "6", "--dry-run", "--workers", "1",
                env=env)
    text = " ".join((r.stdout + r.stderr).split())
    assert "WARNING" in text and "1 TIFF(s) left out in folder(s) named tiff16" in text, \
        text[-2500:]
    r = _script("jxl_recompressor.py", lib, "--mode", "3", "--dry-run", "--workers", "1",
                env=env)
    text = " ".join((r.stdout + r.stderr).split())
    assert "1 JXL(s) left out in folder(s) named jxl" in text, text[-2500:]


# --- S6: in-place layouts the wizard did not recognise ------------------------------

def test_s6_a_run_pointed_at_its_own_output_folder_is_in_place(tmp_path):
    """Mode 5 run AT a folder named JXL_recompressed and mode 6 at
    <marker>/16B_JXL_small put every file back on itself; the wizard knew
    modes 0/8 and mode 2 only, sent no --delete-confirm-off and asked
    nothing, and an unattended preset ended with exit 3 (Fable's S6)."""
    import jxl_photo as wp
    lib = tmp_path / HOSTILE / "lib"
    m6 = lib / "shoot" / "_EXPORT" / "16B_JXL_small"
    m5 = lib / "event (1)" / "JXL_recompressed"
    for d in (m6, m5):
        d.mkdir(parents=True)
        (d / "a (1).jxl").write_bytes(b"x")
    assert wp._jxl_run_replaces_in_place("jxl", "jxl", 6, str(m6), None) is True
    assert wp._jxl_run_replaces_in_place("jxl", "jxl", 5, str(m5), None) is True
    assert wp._jxl_run_replaces_in_place("jxl", "jxl", 6, str(lib), None) is False
    assert wp._jxl_run_replaces_in_place("jxl", "jxl", 5, str(lib), None) is False
    assert wp._jxl_run_replaces_in_place("jxl", "jxl", 3, str(m5), None) is False


# --- C2: a Ctrl+C in the encoder's planning waited for every queued analysis --------

def test_c2_a_ctrl_c_in_the_planning_stops_the_queued_analyses(tmp_path, monkeypatch):
    """The pool's `with` waited for every TIFF still queued after a press in
    "Analyzing TIFF pages" (37 of 40 analysed after it, Fable's C2)."""
    import signal
    import threading
    import time
    import jxl_tiff_encoder as enc
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    for i in range(12):
        tifffile.imwrite(str(src / f"DSC_{i:04d} (2).tif"),
                         np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")
    real = enc.convert_multipage
    calls = []
    lock = threading.Lock()

    def counting(*a, **k):
        with lock:
            calls.append(1)
            first = len(calls) == 1
        if first:
            signal.raise_signal(signal.SIGINT)
            time.sleep(0.3)
        return real(*a, **k)
    monkeypatch.setattr(enc, "convert_multipage", counting)
    try:
        _main(enc, monkeypatch, src, tmp_path / HOSTILE / "JXL", "--mode", "0",
              "--distance", "0", "--effort", "1", "--workers", "1", "--dry-run")
    except KeyboardInterrupt:
        pass
    assert len(calls) <= 3, len(calls)


# --- C3: two quick Ctrl+C in the decoder's delete loop cut a group -------------------

@needs_codecs
def test_c3_two_presses_in_a_groups_deletes_never_cut_it(tmp_path, monkeypatch):
    """A second press raised between two deletes of one group: page 0 gone,
    pages 1-2 left, the TIFF complete — and every later deleting run refused
    the group (Fable's C3). The group's deletes finish, then the presses act."""
    import signal
    import jxl_tiff_decoder as dec
    root = tmp_path / HOSTILE
    masters = _three_page_masters(root)
    real_unlink = Path.unlink
    pressed = []

    def unlink_then_press_twice(self, *a, **k):
        real_unlink(self, *a, **k)
        if self.parent == masters and not pressed:
            pressed.append(self.name)
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
    monkeypatch.setattr(Path, "unlink", unlink_then_press_twice)
    try:
        _main(dec, monkeypatch, masters, root / "TIFF_back", "--mode", "0", "--workers", "1",
              "--delete-source", "--delete-confirm-off", "--no-preview")
    except KeyboardInterrupt:
        pass
    left = sorted(p.name for p in masters.glob("*.jxl"))
    assert pressed, "the gate deleted nothing"
    assert len(left) in (0, 3), left


# --- C5: a killed ExifTool's temp was never swept, and was a serve "output" ----------

@pytest.mark.parametrize("script", ["jxl_tiff_encoder", "jxl_tiff_decoder",
                                    "jxl_jpeg_transcoder", "jxl_recompressor"])
def test_c5_exiftools_own_temp_of_our_temp_is_an_orphan(tmp_path, script):
    """An ExifTool killed while it rewrote '<uuid>_<name>.tif.tmp' leaves
    '<uuid>_<name>.tif.tmp_exiftool_tmp' (8.7 MB in Fable's run): no sweep
    knew the name, and a serve job answered it as an output (C5)."""
    import importlib
    mod = importlib.import_module(script)
    out = tmp_path / HOSTILE
    out.mkdir(parents=True)
    final = out / "大きい 50%.tif"
    left = out / f"{'fb0a65f2' * 4}_{final.name}.tmp_exiftool_tmp"
    left.write_bytes(b"x" * 64)
    other = out / f"{'fb0a65f2' * 4}_other.tif.tmp_exiftool_tmp"
    other.write_bytes(b"x")
    assert mod._sweep_orphan_temps([final]) == 1
    assert not left.exists() and other.exists()


# --- C1: the [D] panel promised deletions the children no longer make ---------------

@pytest.mark.parametrize("dest,conv,want", [
    ("tiff", "jxl_to_tiff", "Kept anyway: JPEG archives"),
    ("png", "jxl_to_png", "masters that carry their TIFF's own tags"),
])
def test_c1_the_delete_panel_says_what_the_scripts_keep(tmp_path, monkeypatch, dest, conv,
                                                        want):
    """Restore TIFFs / Export PNG 16-bit + [D]: the panel said every JXL would
    be deleted; the children keep JPEG archives (#745) and, for a PNG, masters
    with their TIFF's tags (#734) — Fable's C1."""
    import jxl_photo as wp
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    monkeypatch.setattr(menu, "_ask_delete_options", lambda *a, **k: None)
    ui = wp.ScriptedUI({"delete.continue": True, "delete.layout": "0", "delete.sure": True})
    menu._ui = ui
    folder = tmp_path / HOSTILE
    folder.mkdir()
    wf = {"origin_format": "jxl", "dest_format": dest, "input_dir": str(folder),
          "mode": None, "conversion_type": conv, "bit_depth": 16}
    modes = [(str(i), f"mode {i}", "") for i in range(9)]
    assert menu._wizard_delete_gateway(wf, modes) is True
    shown = " ".join(" ".join(ui.output).split())
    assert want in shown, shown[-1500:]


# --- review M2: S3 must not open --restamp-then-sync to a script set to delete ------

def test_review_m2_restamp_then_sync_refuses_a_script_set_to_delete(tmp_path, monkeypatch):
    """The refusal read the flag alone after S3: with DELETE_SOURCE = True at
    the top of the script, --restamp-then-sync --delete-skipped ran deleting,
    and a record rewritten in that run could vouch for a delete (the review of
    rc8's fixes, M2). A lone --delete-skipped without a delete stays inert."""
    import jxl_tiff_encoder as enc
    lib = tmp_path / HOSTILE
    lib.mkdir()
    monkeypatch.setitem(enc._RUN_DEFAULTS, "DELETE_SOURCE", True)
    code = _main(enc, monkeypatch, lib, "--mode", "8", "--restamp-then-sync",
                 "--delete-skipped", "--dry-run", "--workers", "1")
    assert code == 2


# --- the review of rc8's fixes -----------------------------------------------------

@needs_codecs
def test_review_m1_two_presses_during_the_group_swap_leave_it_whole(tmp_path, monkeypatch):
    """A second Ctrl+C during the in-place swap raised between two pages: page
    0 replaced, pages 1-2 old, page 0's original left as .jxlphoto-swap (the
    review of rc8's fixes, M1). The presses wait until the group is done."""
    import signal
    root = tmp_path / HOSTILE
    masters = _three_page_masters(root)
    before = {p.name: _md5(p) for p in masters.glob("*.jxl")}
    real_replace = os.replace
    pressed = []

    def replace_then_press(src, dst, *a, **k):
        real_replace(src, dst, *a, **k)
        if (str(src).endswith(".tmp") and Path(dst).parent == masters
                and Path(dst).suffix == ".jxl" and not pressed):
            pressed.append(Path(dst).name)
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
    monkeypatch.setattr(os, "replace", replace_then_press)
    try:
        _main(rec, monkeypatch, masters, "--mode", "8", "--distance", "2", "--effort", "1",
              "--workers", "1", "--no-keep-smaller", "--on-downgrade", "convert",
              "--on-regeneration", "convert", "--on-unknown", "convert",
              "--delete-confirm-off")
    except KeyboardInterrupt:
        pass
    after = {p.name: _md5(p) for p in masters.glob("*.jxl")}
    changed = [n for n in before if after.get(n) != before[n]]
    assert pressed, "the swap never ran"
    assert set(after) == set(before) and len(changed) in (0, 3), (changed, after)
    assert not list(masters.glob("*.jxlphoto-swap")), list(masters.iterdir())


@needs_codecs
def test_review_w3_in_place_a_page_whose_marks_were_not_read_is_kept(tmp_path, monkeypatch):
    """In place, replacing the source IS deleting it: a group whose marks could
    not be read was replaced anyway (the review of rc8's fixes, W3 in place)."""
    root = tmp_path / HOSTILE
    masters = _three_page_masters(root)
    before = {p.name: _md5(p) for p in masters.glob("*.jxl")}
    _origin_read_fails(monkeypatch)
    _main(rec, monkeypatch, masters, "--mode", "8", "--distance", "2", "--effort", "1",
          "--workers", "1", "--no-keep-smaller", "--on-downgrade", "convert",
          "--on-regeneration", "convert", "--on-unknown", "convert", "--delete-confirm-off")
    assert {p.name: _md5(p) for p in masters.glob("*.jxl")} == before


def test_review_s6_the_wizards_in_place_answers_agree(tmp_path):
    """_policy_delete and the manifest asked _recompress_entry_in_place, which
    knew modes 0/2/8 only, while the confirmation asked the plan: an attended
    run was confirmed twice, a manifest row reached the child's question."""
    import jxl_photo as wp
    lib = tmp_path / HOSTILE / "lib"
    m6 = lib / "shoot" / "_EXPORT" / "16B_JXL_small"
    m5 = lib / "event (1)" / "JXL_recompressed"
    for d in (m6, m5):
        d.mkdir(parents=True)
        (d / "a (1).jxl").write_bytes(b"x")
    for folder, mode, want in ((m6, 6, True), (m5, 5, True), (lib, 6, False), (lib, 5, False)):
        a = wp._jxl_run_replaces_in_place("jxl", "jxl", mode, str(folder), None)
        b = wp._recompress_entry_in_place(str(folder), None, mode)
        assert a == b == want, (folder, mode, a, b)
    # A marker typed in the expert flags moves the plan as it moves the run.
    other = lib / "x" / "_DELIVERY" / "16B_JXL_small"
    other.mkdir(parents=True)
    assert wp._recompress_entry_in_place(str(other), None, 6) is False
    assert wp._recompress_entry_in_place(str(other), None, 6, None,
                                         "--export-marker _DELIVERY") is True


@needs_codecs
def test_review_one_press_stops_the_gates_checks(tmp_path, monkeypatch):
    """W1 moved the recompressor's gate inside the armed handler, but its first
    pass never asked _interrupted: after one press every round-trip check
    still ran, then everything was kept (the review of rc8's fixes)."""
    import signal
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    for i in range(3):
        tifffile.imwrite(str(src / f"DSC_{i:04d} (2).tif"), np.random.default_rng(i).integers(
            0, 65535, (48, 64, 3), dtype=np.uint16), photometric="rgb")
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    common_args = ["--mode", "0", "--distance", "2", "--effort", "1", "--workers", "1",
                   "--on-downgrade", "convert", "--on-regeneration", "convert",
                   "--on-unknown", "convert"]
    r = _script("jxl_recompressor.py", masters, tmp_path / HOSTILE / "small", *common_args)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    calls = []

    def verify(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            signal.raise_signal(signal.SIGINT)
        return True, "ok"
    monkeypatch.setattr(rec, "_verify_roundtrip_jxl", verify)
    try:
        _main(rec, monkeypatch, masters, tmp_path / HOSTILE / "small", *common_args,
              "--delete-source", "--delete-skipped", "--verify-roundtrip",
              "--delete-confirm-off")
    except KeyboardInterrupt:
        pass
    assert len(calls) == 1, len(calls)
    assert len(list(masters.glob("*.jxl"))) == 3


def test_review_l3_padding_and_a_motion_photo_set_to_0(tmp_path):
    """A CRLF or a doubled EOI after the picture is fill, and MotionPhoto="0"
    says there is no video (the review of rc8's fixes)."""
    import jxl_jpeg_transcoder as tr
    base = _jpeg(tmp_path / "b.jpg").read_bytes()
    for tail in (b"\r\n", b"\xff\xd9", b"\x00" * 64):
        f = tmp_path / "padded.jpg"
        f.write_bytes(base + tail)
        assert tr._jpeg_trailer_reason(f) is None, tail
    x = (b'http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta><rdf:Description '
         b'GCamera:MotionPhoto="0"/></x:xmpmeta>')
    seg = b"\xff\xe1" + (len(x) + 2).to_bytes(2, "big") + x
    f = tmp_path / "mp0.jpg"
    f.write_bytes(base[:2] + seg + base[2:])
    assert tr._jpeg_trailer_reason(f) is None
    f.write_bytes(base[:2] + seg.replace(b'"0"', b'"1"') + base[2:])
    assert tr._jpeg_trailer_reason(f) == "a Motion Photo's video"
