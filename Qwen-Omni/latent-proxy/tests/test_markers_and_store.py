import json
import os
import struct
import tempfile
import unittest

from latent_proxy.extractors import StubExtractor
from latent_proxy.markers import find_task_id, strip_markers
from latent_proxy.store import FileLatentStore


class MarkerTest(unittest.TestCase):
    def test_last_marker_wins_and_steering_keeps_the_task(self):
        messages = [
            {"role": "system", "content": "persona"},
            {"role": "user", "content": '<latent_task id="task_1"/>\nfirst'},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [{"type": "text", "text": '<latent_task id="task_2"/>\nsecond'}]},
            {"role": "user", "content": "steer: shorter please"},
        ]
        self.assertEqual(find_task_id(messages), "task_2")
        self.assertIsNone(find_task_id([{"role": "user", "content": "plain"}]))

    def test_strip_removes_every_marker_without_mutating_input(self):
        messages = [
            {"role": "user", "content": '<latent_task id="task_1"/>\nfirst'},
            {"role": "user", "content": [{"type": "text", "text": 'a <latent_task id="task_2"/> b'}]},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
        ]
        cleaned = strip_markers(messages)
        self.assertEqual(cleaned[0]["content"], "first")
        self.assertEqual(cleaned[1]["content"][0]["text"], "a  b")
        self.assertEqual(cleaned[2], messages[2])
        self.assertIn("latent_task", messages[0]["content"])

    def test_invalid_ids_are_not_markers(self):
        self.assertIsNone(find_task_id([{"role": "user", "content": '<latent_task id="../x"/>'}]))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FileLatentStore(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_writes_payload_and_manifest_in_the_gateway_layout(self):
        latent = StubExtractor(tokens=2, dim=3).extract([], "answer")
        manifest = self.store.write(
            "task_1", latent.payload, tokens=2, dim=3, dtype="float32",
            model="stub", layer=-1, final=True,
        )
        task_directory = os.path.join(self.directory.name, "task_1")
        self.assertEqual(sorted(os.listdir(task_directory)), ["1.bin", "1.json"])
        with open(os.path.join(task_directory, "1.json"), encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved, manifest)
        self.assertEqual(saved["payload"], "1.bin")
        self.assertEqual(saved["byte_length"], 2 * 3 * 4)
        self.assertEqual(os.path.getsize(os.path.join(task_directory, "1.bin")), 24)

        second = self.store.write(
            "task_1", latent.payload, tokens=2, dim=3, dtype="float32",
            model="stub", layer=-1, final=False,
        )
        self.assertEqual(second["seq"], 2)

    def test_rejects_unsafe_ids_and_wrong_sizes(self):
        with self.assertRaises(ValueError):
            self.store.write("../x", b"\0" * 4, tokens=1, dim=1, dtype="float32", model="m", layer=0, final=True)
        with self.assertRaises(ValueError):
            self.store.write("task_1", b"\0" * 3, tokens=1, dim=1, dtype="float32", model="m", layer=0, final=True)
        with self.assertRaises(ValueError):
            self.store.write("task_1", b"\0" * 4, tokens=1, dim=1, dtype="int8", model="m", layer=0, final=True)


class StubExtractorTest(unittest.TestCase):
    def test_is_deterministic_and_bounded(self):
        extractor = StubExtractor(tokens=4, dim=8)
        first = extractor.extract([], "same answer")
        self.assertEqual(first.payload, extractor.extract([], "same answer").payload)
        self.assertNotEqual(first.payload, extractor.extract([], "other answer").payload)
        values = struct.unpack("<32f", first.payload)
        self.assertTrue(all(-1.0 <= value < 1.0 for value in values))
        self.assertEqual((first.tokens, first.dim, first.dtype), (4, 8, "float32"))


if __name__ == "__main__":
    unittest.main()
