import tempfile
import unittest
from pathlib import Path
from scripts.audit_export import audit

class ExportTests(unittest.TestCase):
    def test_multiline_csv_and_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'table.csv').write_text('id,note\n1,"a\nb"\n',encoding='utf-8')
            report=audit(root)
            self.assertEqual(report['files'][0]['rows'],1)
            self.assertFalse(report['manifest_match'])
            item=report['files'][0]
            expected=[{'file':item['file'],'rows':1,'sha256':item['sha256']}]
            self.assertTrue(audit(root,expected)['manifest_match'])
            self.assertEqual(audit(root,expected)['transactional_consistency'],'not_verified')

    def test_bad_csv_and_missing_file_not_complete(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'bad.csv').write_text('a,b\n1\n')
            report=audit(root,[{'file':'missing.csv','rows':1,'sha256':'x'}])
            self.assertFalse(report['manifest_match'])
            self.assertFalse(report['files'][0]['parse_ok'])
