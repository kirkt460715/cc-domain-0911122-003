"""Safe loading of the OOXML parts that the repository protocol allows.

Only the WordprocessingML parts named in docs/来源协议.md are opened:
``word/document.xml``, ``word/numbering.xml``, ``word/footnotes.xml`` and the
document relationships.  Everything else stays inside the zip archive and is
listed as out-of-scope evidence rather than parsed.  The archive is treated as
untrusted input: size and member counts are bounded, absolute paths and path
traversal are rejected, XML entities and DTDs are refused, and no relationship
target is ever dereferenced.
"""
import hashlib
import io
import posixpath
import zipfile
from xml.etree import ElementTree as ET

from corpus_gate.errors import GateError, fail

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'

DOCUMENT_PART = 'word/document.xml'
NUMBERING_PART = 'word/numbering.xml'
FOOTNOTES_PART = 'word/footnotes.xml'
DOCUMENT_RELS = 'word/_rels/document.xml.rels'
SCOPED_PARTS = (DOCUMENT_PART, NUMBERING_PART, FOOTNOTES_PART, DOCUMENT_RELS)

MAX_MEMBERS = 10000
MAX_PART_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024

RELATIONSHIP_TYPES = {
    f'{R_NS}/numbering': 'numbering',
    f'{R_NS}/footnotes': 'footnotes',
    f'{R_NS}/hyperlink': 'hyperlink',
    f'{R_NS}/header': 'header',
    f'{R_NS}/footer': 'footer',
    f'{R_NS}/comments': 'comments',
}
IGNORED_RELATIONSHIP_KINDS = ('header', 'footer', 'comments')


class _EntityDefender(ET.TreeBuilder):
    """TreeBuilder that rejects DOCTYPE declarations and entity expansion."""

    def doctype(self, name, pubid, system):
        fail('docx_xml_entity', 'DOCTYPE declarations are not supported in OOXML parts')


def parse_xml(data, part):
    try:
        return ET.fromstring(data, parser=ET.XMLParser(target=_EntityDefender()))
    except GateError:
        raise
    except (ET.ParseError, ValueError, UnicodeError) as exc:
        raise GateError('docx_xml_parse', 'OOXML part is not well-formed XML', part=part) from exc


def _safe_member(name):
    if not name or name.endswith('/'):
        return False
    normalized = posixpath.normpath(name)
    if name.startswith('/') or name.startswith('\\') or '\\' in name:
        return False
    if normalized.startswith('..') or posixpath.isabs(normalized):
        return False
    return normalized == name


def load(raw):
    """Read a docx byte string and return the scoped parts plus audit evidence."""
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        fail('docx_bytes', 'DOCX input must be a nonempty byte string')
    if len(raw) > MAX_PART_BYTES:
        fail('docx_package_size', 'DOCX archive exceeds the size limit')
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(raw)))
    except (zipfile.BadZipFile, OSError) as exc:
        raise GateError('docx_package', 'Input is not a readable OOXML zip package') from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            fail('docx_package_members', 'DOCX archive contains too many members')
        total = 0
        for info in infos:
            if not _safe_member(info.filename):
                fail('docx_member_path', 'Archive member path is outside the package', member=info.filename)
            total += info.file_size
            if info.file_size > MAX_PART_BYTES or total > MAX_TOTAL_BYTES:
                fail('docx_package_size', 'DOCX part exceeds the size limit', member=info.filename)
        names = {info.filename for info in infos}
        missing = [part for part in (DOCUMENT_PART, DOCUMENT_RELS) if part not in names]
        if missing:
            fail('docx_part_missing', 'Required OOXML part is absent', parts=missing)
        payload = {}
        for part in SCOPED_PARTS:
            if part in names:
                payload[part] = archive.read(part)
        ignored_parts = sorted(name for name in names if name not in SCOPED_PARTS)
    evidence = {
        'member_count': len(infos),
        'members': sorted(names),
        'ignored_parts': ignored_parts,
        'package_sha256': hashlib.sha256(bytes(raw)).hexdigest(),
        'scoped_parts': sorted(part for part in payload),
    }
    roots = {part: parse_xml(payload[part], part) for part in payload}
    relationships = _relationships(roots.get(DOCUMENT_RELS))
    return {
        'evidence': evidence,
        'document': roots[DOCUMENT_PART],
        'numbering': roots.get(NUMBERING_PART),
        'footnotes': roots.get(FOOTNOTES_PART),
        'relationships': relationships,
    }


def _relationships(rels_root):
    result = {}
    if rels_root is None:
        return result
    for node in rels_root:
        if not node.tag.endswith('}Relationship'):
            continue
        rel_id = node.get('Id')
        rel_type = node.get('Type', '')
        target = node.get('Target', '')
        external = node.get('TargetMode') == 'External'
        kind = RELATIONSHIP_TYPES.get(rel_type, 'unknown')
        result[rel_id] = {'id': rel_id, 'kind': kind, 'external': external, 'target': target}
    return result
