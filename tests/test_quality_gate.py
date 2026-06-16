"""Tests for the deterministic quality gate.

Stdlib unittest (no pytest dependency — matches harness-runner's zero-dep policy).
Run from the repo root:
    PYTHONPATH=src python3 -m unittest tests.test_quality_gate -v
or:
    PYTHONPATH=src python3 tests/test_quality_gate.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from harness_runner.quality_gate import quality_gate  # noqa: E402

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solid_image(path: Path, size=(200, 200), color=(40, 40, 200)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")


def _real_image(path: Path, size=(200, 200)) -> None:
    """A non-placeholder image: many distinct colors + larger than the size cutoff."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = ((x * 7) % 256, (y * 11) % 256, ((x + y) * 13) % 256)
    im.save(path, "JPEG", quality=95)


class ConfigDriftTest(unittest.TestCase):
    def _project(self, tmp: str, proxy_port: str, backend_port: str) -> Path:
        p = Path(tmp)
        _write(p / "frontend" / "vite.config.ts",
                "export default { server: { proxy: { '/api': "
                f"{{ target: 'http://localhost:{proxy_port}' }} }} }}")
        _write(p / "init.sh", f"#!/bin/bash\nBACKEND_PORT={backend_port}\n"
               'uvicorn app.main:app --port "$BACKEND_PORT"\n')
        return p

    def test_mismatch_is_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = quality_gate(self._project(tmp, "8001", "8000"))
            self.assertEqual(res.verdict, "fail")
            self.assertTrue(any(i.category == "config-drift" and i.severity == "high"
                                for i in res.issues))

    def test_match_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = quality_gate(self._project(tmp, "8000", "8000"))
            self.assertFalse(any(i.category == "config-drift" for i in res.issues))

    def test_no_backend_port_no_false_positive(self):
        # proxy present but backend port undeterminable → must not flag
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _write(p / "frontend" / "vite.config.ts",
                   "export default { server: { proxy: { '/api': "
                   "{ target: 'http://localhost:8001' } } } }")
            res = quality_gate(p)
            self.assertFalse(any(i.category == "config-drift" for i in res.issues))


@unittest.skipUnless(_HAS_PIL, "PIL not available")
class PlaceholderImageTest(unittest.TestCase):
    def test_many_solid_images_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            for i in range(6):
                _solid_image(p / "backend" / "data" / "images" / f"item_{i}.jpg")
            res = quality_gate(p)
            self.assertEqual(res.verdict, "fail")
            self.assertTrue(any(i.category == "placeholder-image" and i.severity == "high"
                                for i in res.issues))

    def test_real_images_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            for i in range(6):
                _real_image(p / "backend" / "data" / "images" / f"item_{i}.jpg")
            res = quality_gate(p)
            self.assertFalse(any(i.category == "placeholder-image" for i in res.issues))


class PlaceholderTextTest(unittest.TestCase):
    def test_lorem_ipsum_in_data_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _write(p / "data" / "items.json",
                   '[{"name": "X", "description": "Lorem ipsum dolor sit amet"}]')
            res = quality_gate(p)
            self.assertTrue(any(i.category == "placeholder-text" for i in res.issues))

    def test_clean_data_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _write(p / "data" / "items.json",
                   '[{"name": "Colosseum", "description": "A Roman amphitheatre."}]')
            res = quality_gate(p)
            self.assertEqual(res.verdict, "pass")


class EmptyProjectTest(unittest.TestCase):
    def test_empty_project_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(quality_gate(Path(tmp)).verdict, "pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)
