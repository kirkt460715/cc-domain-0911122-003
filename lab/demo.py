import tempfile
from pathlib import Path
from zipfile import ZipFile
from .documents import build
with tempfile.TemporaryDirectory() as d:
    with ZipFile(build(Path(d)/'manual.docx')) as z:print('维护文档成员',z.namelist());print(z.read('word/document.xml').decode())
