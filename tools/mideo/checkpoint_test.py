"""不编译原生代码，验证续编输入核验和时间戳恢复边界。"""
import importlib.util
from pathlib import Path
import os
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('checkpoint', Path(__file__).with_name('checkpoint.py'))
checkpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpoint)

class CheckpointTest(unittest.TestCase):
    def test_restore_and_reject_changed_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'src'
            root.mkdir()
            path = root / 'input.cc'
            path.write_text('original')
            os.utime(path, ns=(1000000000, 1000000000))
            manifest = Path(directory) / 'inputs.gz'
            checkpoint.snapshot(root, manifest, 'commit')
            os.utime(path, None)
            checkpoint.restore(root, manifest, 'commit')
            self.assertEqual(path.stat().st_mtime_ns, 1000000000)
            path.write_text('modified')
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                checkpoint.restore(root, manifest, 'commit')
            with self.assertRaisesRegex(RuntimeError, 'commit'):
                checkpoint.restore(root, manifest, 'other')

    def test_timestamp_sentinel_does_not_weaken_source_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'src'
            folder = root / 'third_party/depot_tools'
            folder.mkdir(parents=True)
            marker = folder / '.disable_auto_update'
            marker.write_text('Disabled by script at yesterday')
            manifest = Path(directory) / 'inputs.gz'
            checkpoint.snapshot(root, manifest, 'commit')
            marker.write_text('Disabled by script at today')
            checkpoint.restore(root, manifest, 'commit')
            marker.write_text('unexpected')
            with self.assertRaisesRegex(RuntimeError, 'sentinel'):
                checkpoint.restore(root, manifest, 'commit')

    def test_cipd_slot_relocation_requires_identical_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'src'
            packages = root / 'third_party/depot_tools/.cipd_bin/.cipd/pkgs'
            old = packages / '5' / 'instance'
            old.mkdir(parents=True)
            (old / 'tool').write_text('binary fixture')
            manifest = Path(directory) / 'inputs.gz'
            checkpoint.snapshot(root, manifest, 'commit')
            (packages / '5').rename(packages / '9')
            checkpoint.restore(root, manifest, 'commit')
            (packages / '9/instance/tool').write_text('changed binary')
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                checkpoint.restore(root, manifest, 'commit')

    def test_excludes_outputs_and_rejects_missing_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'src'
            root.mkdir()
            (root / 'out').mkdir()
            (root / 'out' / 'object.o').write_text('not input')
            path = root / 'input.cc'
            path.write_text('source')
            self.assertEqual(list(checkpoint.inputs(root)), [path])
            manifest = Path(directory) / 'inputs.gz'
            checkpoint.snapshot(root, manifest, 'commit')
            path.unlink()
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                checkpoint.restore(root, manifest, 'commit')

if __name__ == '__main__':
    unittest.main()
