"""Synthetic OOXML package builder for DOCX intake tests.

Each generated package lives in its own empty temporary directory and is only
read through the production loader, so tests exercise the same path as real
operator files.  The builder covers tracked changes, nested/restarted numbering,
gridSpan/vMerge tables, referenced and unreferenced footnotes, comments,
headers, visible hyperlinks and explicitly unsupported structures.
"""
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lab.documents import R, W

CONTENT_TYPES = (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
    '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
    '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
    '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
    '</Types>'
)
ROOT_RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>'
)


def paragraph(text, num=None, ins=None, deleted=None, move_to=None, move_from=None, footnote=None, hyperlink=None):
    prefix = ''
    if num is not None:
        num_id, ilvl = num
        prefix = f'<w:pPr><w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num_id}"/></w:numPr></w:pPr>'
    content = ''
    if deleted is not None:
        content += f'<w:del w:id="{deleted[0]}"><w:r><w:delText>{deleted[1]}</w:delText></w:r></w:del>'
    if ins is not None:
        content += f'<w:ins w:id="{ins[0]}"><w:r><w:t>{ins[1]}</w:t></w:r></w:ins>'
    if move_from is not None:
        content += f'<w:moveFrom w:id="{move_from[0]}"><w:r><w:t>{move_from[1]}</w:t></w:r></w:moveFrom>'
    if move_to is not None:
        content += f'<w:moveTo w:id="{move_to[0]}"><w:r><w:t>{move_to[1]}</w:t></w:r></w:moveTo>'
    if text:
        content += f'<w:r><w:t>{text}</w:t></w:r>'
    if footnote is not None:
        content += f'<w:r><w:footnoteReference w:id="{footnote}"/></w:r>'
    if hyperlink is not None:
        content += f'<w:hyperlink r:id="link1"><w:r><w:t>{hyperlink}</w:t></w:r></w:hyperlink>'
    return f'<w:p>{prefix}{content}</w:p>'


def cell(paragraphs, grid_span=None, vmerge=None):
    properties = ''
    if grid_span or vmerge:
        details = ''
        if grid_span:
            details += f'<w:gridSpan w:val="{grid_span}"/>'
        if vmerge:
            details += f'<w:vMerge w:val="{vmerge}"/>' if vmerge != 'continue' else '<w:vMerge/>'
        properties = f'<w:tcPr>{details}</w:tcPr>'
    return f'<w:tc>{properties}{"".join(paragraphs)}</w:tc>'


def table(rows):
    grid = '<w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid>'
    body = ''.join(f'<w:tr>{"".join(cells)}</w:tr>' for cells in rows)
    return f'<w:tbl><w:tblPr/>{grid}{body}</w:tbl>'


def numbering(abstract, nums):
    level_nodes = []
    for ilvl, spec in enumerate(abstract):
        level_nodes.append(
            f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="{spec.get("start", 1)}"/>'
            f'<w:numFmt w:val="{spec["fmt"]}"/><w:lvlText w:val="{spec["text"]}"/></w:lvl>'
        )
    num_nodes = []
    for num_id, abstract_id, overrides in nums:
        override_xml = ''.join(
            f'<w:lvlOverride w:ilvl="{ilvl}"><w:startOverride w:val="{start}"/></w:lvlOverride>'
            for ilvl, start in overrides
        )
        num_nodes.append(f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="{abstract_id}"/>{override_xml}</w:num>')
    return (
        f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0">'
        + ''.join(level_nodes) + '</w:abstractNum>' + ''.join(num_nodes) + '</w:numbering>'
    )


def footnotes(notes):
    body = ''.join(f'<w:footnote w:id="{note_id}"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:footnote>' for note_id, text in notes)
    return f'<w:footnotes xmlns:w="{W}">{body}</w:footnotes>'


def build(document_body, numbering_xml=None, footnotes_xml=None, extra_parts=None, relationships=None, name='sample.docx'):
    numbering_xml = numbering_xml or f'<w:numbering xmlns:w="{W}"/>'
    footnotes_xml = footnotes_xml or f'<w:footnotes xmlns:w="{W}"/>'
    document = f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{document_body}<w:sectPr><w:headerReference w:type="default" r:id="header1"/></w:sectPr></w:body></w:document>'
    rels = {
        'num': (f'{R}/numbering', 'numbering.xml', False),
        'notes': (f'{R}/footnotes', 'footnotes.xml', False),
        'header1': (f'{R}/header', 'header1.xml', False),
        'link1': (f'{R}/hyperlink', 'https://example.invalid/manual', True),
        'comment1': (f'{R}/comments', 'comments.xml', False),
    }
    if relationships:
        rels.update(relationships)
    rel_nodes = []
    for rid, (kind, target, external) in rels.items():
        external_attr = ' TargetMode="External"' if external else ''
        rel_nodes.append(f'<Relationship Id="{rid}" Type="{kind}" Target="{target}"{external_attr}/>')
    rel_xml = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(rel_nodes) + '</Relationships>'
    parts = {
        '[Content_Types].xml': CONTENT_TYPES,
        '_rels/.rels': ROOT_RELS,
        'word/document.xml': document,
        'word/numbering.xml': numbering_xml,
        'word/footnotes.xml': footnotes_xml,
        'word/_rels/document.xml.rels': rel_xml,
        'word/header1.xml': f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>内部页眉</w:t></w:r></w:p></w:hdr>',
        'word/comments.xml': f'<w:comments xmlns:w="{W}"><w:comment w:id="1" w:author="x"><w:p><w:r><w:t>批注内容</w:t></w:r></w:p></w:comment></w:comments>',
    }
    if extra_parts:
        parts.update(extra_parts)
    folder = tempfile.mkdtemp(prefix='docx-case-')
    path = Path(folder) / name
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for member, value in parts.items():
            archive.writestr(member, value)
    return path


def standard_numbering():
    abstract = [
        {'fmt': 'decimal', 'text': '%1.', 'start': 1},
        {'fmt': 'decimal', 'text': '%1.%2', 'start': 1},
    ]
    return numbering(abstract, [('1', '0', []), ('2', '0', [(0, 5)])])
