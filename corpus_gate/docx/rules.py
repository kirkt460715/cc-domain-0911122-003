"""Deterministic WordprocessingML extraction rules.

Two operator-selected views are supported:

* ``accepted`` — the body after tracked changes are accepted: insertions
  (``w:ins``) and move targets (``w:moveTo``) are kept, deletions
  (``w:del``) and move sources (``w:moveFrom``) are dropped.
* ``original`` — the body before the revisions: deletions and move sources are
  kept, insertions and move targets are dropped.

Every emitted block carries an OOXML source location (paragraph/cell path) and
a content kind.  Footnotes are expanded once at their first reference while
later references keep the reference relation without duplicating the text.
Unsupported structures are collected with their location; an extraction that
hits one, or that would produce empty text, is reported rather than delivered
as a seemingly complete document.
"""
import re

from corpus_gate.docx.package import W_NS, R_NS, IGNORED_RELATIONSHIP_KINDS
from corpus_gate.errors import fail

RULE_NAME = 'corpus-gate-ooxml-wordprocessingml'
RULE_VERSION = 1
SUPPORTED_NUM_FORMATS = {'decimal', 'bullet'}
SUPPORTED_VIEWS = ('accepted', 'original')

W = f'{{{W_NS}}}'
R = f'{{{R_NS}}}'

# Structural elements that are deliberately skipped without an unsupported report.
KNOWN_SKIP_TAGS = {
    f'{W}bookmarkStart', f'{W}bookmarkEnd', f'{W}proofErr', f'{W}pPr',
    f'{W}rPr', f'{W}tblPr', f'{W}tblGrid', f'{W}gridCol', f'{W}tcPr',
    f'{W}sectPr', f'{W}headerReference', f'{W}footerReference',
    f'{W}commentRangeStart', f'{W}commentRangeEnd', f'{W}numPr',
    f'{W}numId', f'{W}ilvl',
}
UNSUPPORTED_TAGS = {
    f'{W}drawing', f'{W}pict', f'{W}object', f'{W}AlternateContent',
    f'{W}altChunk', f'{W}commentReference', f'{W}embeddedObject',
}


def rule_descriptor():
    return {
        'rule': RULE_NAME,
        'rule_version': RULE_VERSION,
        'supported_parts': ['word/document.xml', 'word/numbering.xml', 'word/footnotes.xml', 'word/_rels/document.xml.rels'],
        'supported_views': list(SUPPORTED_VIEWS),
        'numbering_formats': sorted(SUPPORTED_NUM_FORMATS),
        'hyperlink_behavior': 'visible_text_only_targets_never_fetched',
        'excluded_content': ['headers', 'footers', 'comments', 'unreferenced_footnotes'],
    }


def _wval(node, name, default=None):
    value = node.get(f'{W}{name}')
    return default if value is None else value


class Numbering:
    """Numbering counters honouring abstract definitions, restarts and overrides."""

    def __init__(self, root, report):
        self.report = report
        self.abstract = {}
        self.nums = {}
        if root is not None:
            self._parse(root)
        self.counters = {}

    def _parse(self, root):
        for abstract in root.findall(f'{W}abstractNum'):
            abstract_id = _wval(abstract, 'abstractNumId')
            levels = {}
            for level in abstract.findall(f'{W}lvl'):
                ilvl = int(_wval(level, 'ilvl', '0'))
                fmt_node = level.find(f'{W}numFmt')
                text_node = level.find(f'{W}lvlText')
                start_node = level.find(f'{W}start')
                levels[ilvl] = {
                    'format': _wval(fmt_node, 'val', 'decimal') if fmt_node is not None else 'decimal',
                    'template': _wval(text_node, 'val', '') if text_node is not None else '',
                    'start': int(_wval(start_node, 'val', '1')) if start_node is not None else 1,
                }
            self.abstract[abstract_id] = levels
        for num in root.findall(f'{W}num'):
            num_id = _wval(num, 'numId')
            link = num.find(f'{W}abstractNumId')
            abstract_id = _wval(link, 'val') if link is not None else None
            overrides = {}
            for override in num.findall(f'{W}lvlOverride'):
                ilvl = int(_wval(override, 'ilvl', '0'))
                start = override.find(f'{W}startOverride')
                if start is not None:
                    overrides[ilvl] = int(_wval(start, 'val'))
            self.nums[num_id] = {'abstract': abstract_id, 'overrides': overrides}

    def label(self, num_id, ilvl, location):
        spec = self.nums.get(num_id)
        if spec is None:
            self.report(location, 'numbering_undefined', f'numId {num_id} has no w:num definition')
            return None
        levels = self.abstract.get(spec['abstract'])
        if levels is None:
            self.report(location, 'numbering_undefined', f'numId {num_id} references missing abstractNum')
            return None
        level = levels.get(ilvl)
        if level is None:
            self.report(location, 'numbering_level', f'numId {num_id} has no level {ilvl}')
            return None
        if level['format'] not in SUPPORTED_NUM_FORMATS:
            self.report(location, 'numbering_format', f"numFmt {level['format']} is outside the protocol scope")
            return None
        key = (num_id, ilvl)
        if key not in self.counters:
            self.counters[key] = spec['overrides'].get(ilvl, level['start'])
        value = self.counters[key]
        if level['format'] == 'bullet':
            label = level['template'] or '•'
        elif level['template']:
            def substitute(match):
                target = int(match.group(1)) - 1
                if target == ilvl:
                    return str(value)
                current = self.counters.get((num_id, target))
                if current is None:
                    target_level = levels.get(target, {})
                    return str(target_level.get('start', 1))
                return str(current - 1)
            label = re.sub(r'%(\d+)', substitute, level['template'])
        else:
            label = str(value)
        self.counters[key] = value + 1
        for deeper in list(self.counters):
            if deeper[0] == num_id and deeper[1] > ilvl:
                del self.counters[deeper]
        return label


class Extraction:
    def __init__(self, package, view):
        if view not in SUPPORTED_VIEWS:
            fail('docx_view', 'Unsupported extraction view', view=view)
        self.view = view
        self.package = package
        self.unsupported = []
        self.blocks = []
        self.footnotes_seen = set()
        self.footnote_text = self._index_footnotes()
        self.numbering = Numbering(package['numbering'], self._unsupported)
        self._body_index = 0
        self._table_index = -1

    def _unsupported(self, location, code, message):
        entry = {'code': code, 'message': message, 'location': location}
        if entry not in self.unsupported:
            self.unsupported.append(entry)

    def _index_footnotes(self):
        root = self.package['footnotes']
        result = {}
        if root is None:
            return result
        for footnote in root.findall(f'{W}footnote'):
            note_id = _wval(footnote, 'id')
            note_type = _wval(footnote, 'type')
            if note_type in {'separator', 'continuationSeparator'}:
                continue
            paragraphs = [self._paragraph_text(paragraph) for paragraph in footnote.findall(f'{W}p')]
            text = '\n'.join(part for part in paragraphs if part).strip()
            result[note_id] = text
        return result

    def run(self):
        body = self.package['document'].find(f'{W}body')
        if body is None:
            self._unsupported('word/document.xml', 'document_body', 'Document has no w:body')
            return self._result()
        for child in body:
            self._body_index += 1
            if child.tag == f'{W}p':
                self._emit_paragraph(child, f'/body/p[{self._body_index}]')
            elif child.tag == f'{W}tbl':
                self._table_index += 1
                self._walk_table(child, f'/body/tbl[{self._table_index + 1}]')
            elif child.tag == f'{W}sectPr':
                continue
            else:
                self._unsupported(f'/body/*[{self._body_index}]', 'unsupported_structure', f'{child.tag} is not supported at body level')
        self._check_relationships()
        return self._result()

    def _check_relationships(self):
        for rel in self.package['relationships'].values():
            if rel['kind'] == 'unknown':
                self._unsupported('word/_rels/document.xml.rels', 'unsupported_relationship', f"relationship {rel['id']} type is outside scope")

    def _walk_table(self, table, path):
        rows = table.findall(f'{W}tr')
        vertical_spans = {}
        for row_index, row in enumerate(rows, 1):
            occupied = set()
            column = 0
            for cell_index, cell in enumerate(row.findall(f'{W}tc'), 1):
                while column in occupied:
                    column += 1
                properties = cell.find(f'{W}tcPr')
                span = 1
                continuation = False
                if properties is not None:
                    grid_span = properties.find(f'{W}gridSpan')
                    if grid_span is not None:
                        span = int(_wval(grid_span, 'val', '1'))
                    merge = properties.find(f'{W}vMerge')
                    if merge is not None:
                        merge_value = _wval(merge, 'val', 'continue')
                        continuation = merge_value == 'continue'
                        if merge_value != 'continue':
                            vertical_spans[column] = span
                cell_path = f'{path}/tr[{row_index}]/tc[{cell_index}]'
                if continuation:
                    for offset in range(vertical_spans.get(column, 1)):
                        occupied.add(column + offset)
                    column += vertical_spans.get(column, 1)
                    continue
                for offset in range(span):
                    occupied.add(column + offset)
                for paragraph_index, paragraph in enumerate(cell.findall(f'{W}p'), 1):
                    self._emit_paragraph(paragraph, f'{cell_path}/p[{paragraph_index}]', table_cell=cell_path)
                column += span

    def _view_kept(self, tag):
        if tag in (f'{W}ins', f'{W}moveTo'):
            return self.view == 'accepted'
        if tag in (f'{W}del', f'{W}moveFrom'):
            return self.view == 'original'
        return True

    def _paragraph_text(self, paragraph):
        """Plain visible text of a footnote/standalone paragraph under the view."""
        parts = []

        def walk(node):
            tag = node.tag
            if tag in (f'{W}ins', f'{W}moveTo', f'{W}del', f'{W}moveFrom'):
                if not self._view_kept(tag):
                    return
            if tag in UNSUPPORTED_TAGS:
                return
            if tag == f'{W}t':
                parts.append(node.text or '')
            elif tag == f'{W}delText':
                if self.view == 'original':
                    parts.append(node.text or '')
            elif tag == f'{W}tab':
                parts.append('\t')
            elif tag == f'{W}br':
                parts.append('\n')
            for child in node:
                walk(child)

        walk(paragraph)
        return ''.join(parts).strip()

    def _emit_paragraph(self, paragraph, location, table_cell=None):
        num_id, ilvl = self._number_reference(paragraph, location)
        label = None
        if num_id is not None:
            label = self.numbering.label(num_id, ilvl, location)
        segments, references = self._collect_runs(paragraph, location)
        text = ''.join(segment['text'] for segment in segments).strip()
        if not text and not references:
            return
        block = {
            'ordinal': len(self.blocks),
            'kind': 'table_cell' if table_cell else ('list_item' if num_id is not None else 'paragraph'),
            'text': text,
            'location': location,
        }
        hyperlink_spans = [segment['text'] for segment in segments if segment['hyperlink'] and segment['text']]
        if hyperlink_spans:
            block['hyperlinks'] = hyperlink_spans
        if table_cell:
            block['table_cell'] = table_cell
        if num_id is not None:
            block['numbering'] = {'num_id': num_id, 'ilvl': ilvl, 'label': label}
        self.blocks.append(block)
        for note_id in references:
            self._emit_footnote(note_id, location)

    def _number_reference(self, paragraph, location):
        properties = paragraph.find(f'{W}pPr')
        if properties is None:
            return None, 0
        num_pr = properties.find(f'{W}numPr')
        if num_pr is None:
            return None, 0
        num_id_node = num_pr.find(f'{W}numId')
        ilvl_node = num_pr.find(f'{W}ilvl')
        if num_id_node is None:
            self._unsupported(location, 'numbering_missing', 'numPr without numId')
            return None, 0
        return _wval(num_id_node, 'val'), int(_wval(ilvl_node, 'val', '0')) if ilvl_node is not None else 0

    def _collect_runs(self, node, location):
        output = []
        references = []

        def walk(element, in_hyperlink=False):
            tag = element.tag
            if tag in (f'{W}ins', f'{W}moveTo', f'{W}del', f'{W}moveFrom'):
                if not self._view_kept(tag):
                    return
            elif tag in UNSUPPORTED_TAGS:
                self._unsupported(location, 'unsupported_structure', f'{tag} cannot be extracted')
                return
            elif tag == f'{W}hyperlink':
                rel_id = element.get(f'{R}id')
                relation = self.package['relationships'].get(rel_id)
                if relation is not None and not relation['external']:
                    self._unsupported(location, 'hyperlink_internal', 'Internal hyperlink targets are not resolved')
                for child in element:
                    walk(child, True)
                return
            elif tag == f'{W}footnoteReference':
                references.append(_wval(element, 'id'))
                return
            elif tag == f'{W}t' or (tag == f'{W}delText' and self.view == 'original'):
                output.append({'text': element.text or '', 'hyperlink': in_hyperlink})
            elif tag == f'{W}tab':
                output.append({'text': '\t', 'hyperlink': in_hyperlink})
            elif tag == f'{W}br':
                output.append({'text': '\n', 'hyperlink': in_hyperlink})
            elif tag in KNOWN_SKIP_TAGS or tag in (f'{W}r', f'{W}p', f'{W}ins', f'{W}moveTo', f'{W}del', f'{W}moveFrom') or not tag.startswith(W):
                pass
            else:
                self._unsupported(location, 'unsupported_structure', f'{tag} is outside the supported OOXML scope')
                return
            for child in element:
                walk(child, in_hyperlink)

        walk(node)
        return output, references

    def _emit_footnote(self, note_id, location):
        if note_id not in self.footnote_text:
            self._unsupported(location, 'footnote_missing', f'footnoteReference id {note_id} has no footnote part')
            return
        reference = {'footnote_id': note_id, 'reference_location': location, 'repeated': note_id in self.footnotes_seen}
        if note_id in self.footnotes_seen:
            self.blocks.append({'ordinal': len(self.blocks), 'kind': 'footnote_reference', 'text': '', 'location': location, 'footnote': reference})
            return
        self.footnotes_seen.add(note_id)
        text = self.footnote_text[note_id]
        if not text:
            self._unsupported(f'word/footnotes.xml#footnote-{note_id}', 'footnote_empty', f'footnote {note_id} has no extractable text')
            return
        self.blocks.append({
            'ordinal': len(self.blocks),
            'kind': 'footnote',
            'text': text,
            'location': f'word/footnotes.xml#footnote-{note_id}',
            'footnote': {**reference, 'repeated': False},
        })

    def _result(self):
        text_blocks = [block for block in self.blocks if block['text']]
        text = '\n'.join(block['text'] for block in text_blocks)
        referenced = sorted(self.footnotes_seen, key=lambda value: int(value) if value.isdigit() else value)
        unreferenced = sorted(note_id for note_id in self.footnote_text if note_id not in self.footnotes_seen)
        return {
            'view': self.view,
            'rule': rule_descriptor(),
            'text': text,
            'blocks': self.blocks,
            'unsupported': self.unsupported,
            'ok': not self.unsupported and bool(text),
            'empty_text': not bool(text),
            'footnotes': {'referenced': referenced, 'unreferenced': unreferenced},
            'ignored_relationship_kinds': sorted({rel['kind'] for rel in self.package['relationships'].values() if rel['kind'] in IGNORED_RELATIONSHIP_KINDS}),
        }


def extract(package, view):
    if view not in SUPPORTED_VIEWS:
        fail('docx_view', 'View must be accepted or original', view=view)
    return Extraction(package, view).run()
