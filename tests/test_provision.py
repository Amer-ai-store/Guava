import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.prepare_control import prepare,NAMES

class ProvisionTests(unittest.TestCase):
    def test_private_distinct_credentials_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{name:'' for name in NAMES}):
            paths=prepare(folder)
            self.assertEqual(len(paths),3)
            env=Path(paths[0]).read_text()
            values=dict(line.split('=',1) for line in env.splitlines())
            self.assertEqual(len(set(values.values())),3)
            for path in paths:
                self.assertEqual(Path(path).stat().st_mode & 0o777,0o600)
            with self.assertRaisesRegex(ValueError,'preserved'):
                prepare(folder)
            self.assertEqual(Path(paths[0]).read_text(),env)

    def test_existing_bindings_reused(self):
        existing={name:'existing-'+str(i)+'x'*40 for i,name in enumerate(NAMES)}
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,existing):
            paths=prepare(folder)
            env=Path(paths[0]).read_text()
            self.assertTrue(all(value in env for value in existing.values()))
