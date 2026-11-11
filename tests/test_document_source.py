import tempfile,unittest
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as E
from lab.documents import build,W
class DocumentSource(unittest.TestCase):
    def test_relationships_and_revision_material(self):
        with tempfile.TemporaryDirectory() as d,ZipFile(build(Path(d)/'a.docx')) as z:
            root=E.fromstring(z.read('word/document.xml'))
            self.assertEqual(len(root.findall('.//{'+W+'}ins')),1)
            self.assertEqual(len(root.findall('.//{'+W+'}del')),1)
            self.assertEqual(len(root.findall('.//{'+W+'}footnoteReference')),1)
            self.assertIn(b'startOverride',z.read('word/numbering.xml'))
