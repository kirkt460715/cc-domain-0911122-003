import io
import unittest
from zipfile import ZipFile

from corpus_gate.docx import gate, package, rules
from corpus_gate.docx.rules import RULE_VERSION
from corpus_gate.errors import GateError
from tests import docx_support as ds
from tests.support import Case, authorization, context


def load_view(path, view):
    return rules.extract(package.load(path.read_bytes()), view)


class RevisionViewTests(unittest.TestCase):
    def setUp(self):
        self.path = ds.build(
            ds.paragraph('标题')
            + ds.paragraph('', ins=('2', '新步骤。'), deleted=('1', '旧步骤。'))
            + ds.paragraph('', move_to=('4', '移动文字'), move_from=('3', '移动文字')),
            numbering_xml=ds.standard_numbering(),
        )

    def test_accepted_view_keeps_insertions_and_move_targets(self):
        result = load_view(self.path, 'accepted')
        self.assertTrue(result['ok'], result['unsupported'])
        self.assertEqual(result['text'].split('\n'), ['标题', '新步骤。', '移动文字'])
        kinds = [block['kind'] for block in result['blocks']]
        self.assertEqual(kinds, ['paragraph', 'paragraph', 'paragraph'])
        self.assertNotIn('旧步骤。', result['text'])

    def test_original_view_keeps_deletions_and_move_sources(self):
        result = load_view(self.path, 'original')
        self.assertTrue(result['ok'])
        self.assertEqual(result['text'].split('\n'), ['标题', '旧步骤。', '移动文字'])
        self.assertNotIn('新步骤。', result['text'])

    def test_each_block_carries_a_source_location(self):
        result = load_view(self.path, 'accepted')
        self.assertEqual([block['location'] for block in result['blocks']], ['/body/p[1]', '/body/p[2]', '/body/p[3]'])
        self.assertTrue(all('word/' in block['location'] or block['location'].startswith('/body/') for block in result['blocks']))

    def test_unknown_view_is_rejected(self):
        with self.assertRaises(GateError) as caught:
            rules.extract(package.load(self.path.read_bytes()), 'final')
        self.assertEqual(caught.exception.code, 'docx_view')
        with self.assertRaises(GateError) as caught:
            rules.Extraction(package.load(self.path.read_bytes()), 'final')
        self.assertEqual(caught.exception.code, 'docx_view')

    def test_document_order_mixes_paragraphs_lists_tables_and_footnotes(self):
        body = (
            ds.paragraph('开头')
            + ds.paragraph('列表项', num=('1', 0))
            + ds.table([[ds.cell([ds.paragraph('单元格')])]])
            + ds.paragraph('带注', footnote='2')
        )
        path = ds.build(body, numbering_xml=ds.standard_numbering(), footnotes_xml=ds.footnotes([('2', '注释。')]))
        result = load_view(path, 'accepted')
        self.assertEqual([block['kind'] for block in result['blocks']], ['paragraph', 'list_item', 'table_cell', 'paragraph', 'footnote'])
        self.assertEqual(result['text'].split('\n'), ['开头', '列表项', '单元格', '带注', '注释。'])
        ordinals = [block['ordinal'] for block in result['blocks']]
        self.assertEqual(ordinals, list(range(len(ordinals))))


class NumberingTests(unittest.TestCase):
    def setUp(self):
        body = (
            ds.paragraph('一级甲', num=('1', 0))
            + ds.paragraph('二级甲', num=('1', 1))
            + ds.paragraph('一级乙', num=('1', 0))
            + ds.paragraph('重启项', num=('2', 0))
        )
        self.path = ds.build(body, numbering_xml=ds.standard_numbering())

    def test_nested_labels_restarts_and_overrides(self):
        result = load_view(self.path, 'accepted')
        labels = [block['numbering']['label'] for block in result['blocks']]
        self.assertEqual(labels, ['1.', '1.1', '2.', '5.'])
        self.assertTrue(all(block['kind'] == 'list_item' for block in result['blocks']))
        self.assertEqual(result['blocks'][1]['numbering']['ilvl'], 1)

    def test_unsupported_numbering_format_is_reported_with_location(self):
        abstract = [{'fmt': 'chineseCounting', 'text': '%1', 'start': 1}]
        path = ds.build(ds.paragraph('中式编号', num=('1', 0)), numbering_xml=ds.numbering(abstract, [('1', '0', [])]))
        result = load_view(path, 'accepted')
        self.assertFalse(result['ok'])
        self.assertEqual(result['unsupported'][0]['code'], 'numbering_format')
        self.assertEqual(result['unsupported'][0]['location'], '/body/p[1]')
        self.assertTrue(result['text'])

    def test_missing_num_definition_is_reported(self):
        path = ds.build(ds.paragraph('孤立编号', num=('9', 0)), numbering_xml=ds.standard_numbering())
        result = load_view(path, 'accepted')
        self.assertEqual(result['unsupported'][0]['code'], 'numbering_undefined')


class TableTests(unittest.TestCase):
    def setUp(self):
        rows = [
            [ds.cell([ds.paragraph('横跨两列')], grid_span=2)],
            [ds.cell([ds.paragraph('左上')], vmerge='restart'), ds.cell([ds.paragraph('右上')])],
            [ds.cell([ds.paragraph('应忽略的延续')], vmerge='continue'), ds.cell([ds.paragraph('右下')])],
        ]
        self.path = ds.build(ds.table(rows))

    def test_merged_cells_are_not_duplicated(self):
        result = load_view(self.path, 'accepted')
        texts = [block['text'] for block in result['blocks'] if block['kind'] == 'table_cell']
        self.assertEqual(texts, ['横跨两列', '左上', '右上', '右下'])
        self.assertNotIn('应忽略的延续', result['text'])
        locations = [block['location'] for block in result['blocks']]
        self.assertEqual(locations[0], '/body/tbl[1]/tr[1]/tc[1]/p[1]')
        self.assertEqual(locations[-1], '/body/tbl[1]/tr[3]/tc[2]/p[1]')
        cells = [block['table_cell'] for block in result['blocks']]
        self.assertEqual(cells[0], '/body/tbl[1]/tr[1]/tc[1]')
        self.assertEqual(cells[-1], '/body/tbl[1]/tr[3]/tc[2]')

    def test_gridspan_keeps_single_paragraph_identity(self):
        result = load_view(self.path, 'accepted')
        span_block = result['blocks'][0]
        self.assertEqual(span_block['text'], '横跨两列')
        self.assertEqual(len([b for b in result['blocks'] if b['text'] == '横跨两列']), 1)

    def test_vertical_merge_keeps_adjacent_columns_in_order(self):
        rows = [
            [ds.cell([ds.paragraph('竖并起点')], vmerge='restart'), ds.cell([ds.paragraph('右上一')]), ds.cell([ds.paragraph('右上二')])],
            [ds.cell([ds.paragraph('不应出现')], vmerge='continue'), ds.cell([ds.paragraph('右下一')]), ds.cell([ds.paragraph('右下二')])],
        ]
        result = load_view(ds.build(ds.table(rows)), 'accepted')
        self.assertEqual([b['text'] for b in result['blocks']], ['竖并起点', '右上一', '右上二', '右下一', '右下二'])
        self.assertEqual([b['table_cell'] for b in result['blocks']][-1], '/body/tbl[1]/tr[2]/tc[3]')


class FootnoteTests(unittest.TestCase):
    def setUp(self):
        body = (
            ds.paragraph('首段', footnote='2')
            + ds.paragraph('再次引用', footnote='2')
        )
        notes = ds.footnotes([('2', '脚注正文。'), ('9', '无人引用的脚注。')])
        self.path = ds.build(body, footnotes_xml=notes)

    def test_footnote_expands_once_and_keeps_reference_relation(self):
        result = load_view(self.path, 'accepted')
        kinds = [block['kind'] for block in result['blocks']]
        self.assertEqual(kinds, ['paragraph', 'footnote', 'paragraph', 'footnote_reference'])
        footnote_block = next(block for block in result['blocks'] if block['kind'] == 'footnote')
        self.assertEqual(footnote_block['text'], '脚注正文。')
        self.assertEqual(footnote_block['location'], 'word/footnotes.xml#footnote-2')
        self.assertFalse(footnote_block['footnote']['repeated'])
        reference = next(block for block in result['blocks'] if block['kind'] == 'footnote_reference')
        self.assertTrue(reference['footnote']['repeated'])
        self.assertEqual(reference['footnote']['footnote_id'], '2')
        self.assertEqual(result['text'].count('脚注正文。'), 1)

    def test_unreferenced_footnotes_never_enter_body(self):
        result = load_view(self.path, 'accepted')
        self.assertNotIn('无人引用的脚注', result['text'])
        self.assertEqual(result['footnotes']['referenced'], ['2'])
        self.assertEqual(result['footnotes']['unreferenced'], ['9'])

    def test_empty_and_missing_footnotes_are_reported_with_location(self):
        empty_path = ds.build(ds.paragraph('正文', footnote='2'), footnotes_xml=ds.footnotes([('2', '')]))
        empty_result = load_view(empty_path, 'accepted')
        self.assertFalse(empty_result['ok'])
        self.assertEqual(empty_result['unsupported'][0]['code'], 'footnote_empty')
        missing_path = ds.build(ds.paragraph('正文', footnote='7'), footnotes_xml=ds.footnotes([('2', '存在')]))
        missing_result = load_view(missing_path, 'accepted')
        self.assertEqual(missing_result['unsupported'][0]['code'], 'footnote_missing')
        self.assertEqual(missing_result['unsupported'][0]['location'], '/body/p[1]')


class ExclusionAndHyperlinkTests(unittest.TestCase):
    def test_header_comments_and_hyperlink_targets_are_excluded(self):
        body = ds.paragraph('带链接文字', hyperlink='说明链接')
        path = ds.build(body)
        result = load_view(path, 'accepted')
        self.assertIn('说明链接', result['text'])
        self.assertEqual(result['blocks'][0]['hyperlinks'], ['说明链接'])
        self.assertNotIn('内部页眉', result['text'])
        self.assertNotIn('批注内容', result['text'])
        self.assertNotIn('example.invalid', result['text'])
        self.assertEqual(result['ignored_relationship_kinds'], ['comments', 'header'])

    def test_drawing_and_altchunk_are_unsupported_not_silent(self):
        body = ds.paragraph('前文') + '<w:p><w:r><w:drawing><wp:inline xmlns:wp="x"/></w:drawing></w:r></w:p>'
        path = ds.build(body)
        result = load_view(path, 'accepted')
        self.assertFalse(result['ok'])
        self.assertEqual(result['unsupported'][0]['code'], 'unsupported_structure')
        self.assertIn('drawing', result['unsupported'][0]['message'])
        self.assertIn('前文', result['text'])

    def test_empty_extraction_is_reported_not_delivered(self):
        path = ds.build('')
        result = load_view(path, 'accepted')
        self.assertTrue(result['empty_text'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['text'], '')

    def test_missing_document_part_fails_with_located_error(self):
        path = ds.build(ds.paragraph('x'))
        with ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist() if name != 'word/document.xml'}
        rebuilt = path.parent / 'broken.docx'
        with ZipFile(rebuilt, 'w') as archive:
            for name, value in members.items():
                archive.writestr(name, value)
        with self.assertRaises(GateError) as caught:
            package.load(rebuilt.read_bytes())
        self.assertEqual(caught.exception.code, 'docx_part_missing')

    def test_dtd_is_rejected(self):
        path = ds.build(ds.paragraph('x'))
        evil = path.parent / 'evil.docx'
        with ZipFile(path) as source:
            with ZipFile(evil, 'w') as target:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename == 'word/document.xml':
                        data = b'<!DOCTYPE w:document [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + data
                    target.writestr(info, data)
        with self.assertRaises(GateError) as caught:
            package.load(evil.read_bytes())
        self.assertEqual(caught.exception.code, 'docx_xml_entity')


class PackageSecurityTests(unittest.TestCase):
    def test_path_traversal_member_is_rejected(self):
        buffer = io.BytesIO()
        with ZipFile(buffer, 'w') as archive:
            archive.writestr('../evil.xml', 'x')
            archive.writestr('word/document.xml', '<w:document xmlns:w="x"><w:body/></w:document>')
            archive.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="r"/>')
        with self.assertRaises(GateError) as caught:
            package.load(buffer.getvalue())
        self.assertEqual(caught.exception.code, 'docx_member_path')

    def test_non_zip_input_is_rejected(self):
        with self.assertRaises(GateError) as caught:
            package.load(b'plain text, not a package')
        self.assertEqual(caught.exception.code, 'docx_package')

    def test_out_of_scope_parts_are_listed_not_parsed(self):
        path = ds.build(ds.paragraph('正文'), extra_parts={'word/embeddings/x.bin': b'binary'})
        loaded = package.load(path.read_bytes())
        self.assertIn('word/embeddings/x.bin', loaded['evidence']['ignored_parts'])
        self.assertNotIn('word/embeddings/x.bin', loaded['evidence']['scoped_parts'])


class GateFlowTests(Case):
    def _docx(self):
        return ds.build(
            ds.paragraph('维护步骤')
            + ds.paragraph('', ins=('2', '新步骤。'), deleted=('1', '旧步骤。'))
            + ds.table([[ds.cell([ds.paragraph('表格内容')])]])
            + ds.paragraph('引用段', footnote='2'),
            numbering_xml=ds.standard_numbering(),
            footnotes_xml=ds.footnotes([('2', '脚注内容。'), ('9', '未引用。')]),
        )

    def test_preview_shows_mapping_and_confirm_continues_downstream(self):
        path = self._docx()
        preview = gate.preview_file(self.db, 'source', 'manual', 1, 'accepted', str(path), context())
        self.assertEqual(preview['state'], 'ready')
        self.assertEqual(preview['rule']['rule_version'], RULE_VERSION)
        self.assertEqual(len(preview['evidence']['members']), 8)
        self.assertIn('word/header1.xml', preview['evidence']['ignored_parts'])
        self.assertTrue(all('location' in block for block in preview['blocks']))
        self.assertEqual(preview['authorization']['allowed'], True)
        stored = gate.get_preview(self.db, preview['preview_id'])
        self.assertEqual(stored['text'], preview['text'])
        confirmed = gate.confirm(self.db, preview['preview_id'], 'default', 'docx-batch', context())
        self.assertEqual(confirmed['state'], 'confirmed')
        document = self.document('manual')
        self.assertEqual(document['raw_text'], preview['text'])
        self.assertIn('新步骤。', document['raw_text'])
        self.assertNotIn('旧步骤。', document['raw_text'])
        metadata = document['metadata']
        self.assertEqual(metadata['origin'], 'docx')
        self.assertEqual(metadata['view'], 'accepted')
        self.assertEqual(metadata['package_sha256'], preview['package_sha256'])
        self.assertEqual(len(metadata['source_locations']), len(preview['blocks']))
        self.assertTrue(self.app.dispatch('system.check', {})['ok'])

    def test_two_views_compare_with_overwrite_protection(self):
        path = self._docx()
        accepted = gate.preview_file(self.db, 'source', 'manual', 1, 'accepted', str(path), context())
        original = gate.preview_file(self.db, 'source', 'manual', 1, 'original', str(path), context())
        comparison = gate.compare(self.db, accepted['preview_id'], original['preview_id'])
        self.assertTrue(comparison['same_source_file'])
        self.assertTrue(comparison['view_changed'])
        self.assertTrue(comparison['text_changed'])
        self.assertFalse(comparison['rule_version_changed'])
        gate.confirm(self.db, accepted['preview_id'], 'default', 'accepted-batch', context())
        self.fails('document_old_version', gate.confirm, self.db, original['preview_id'], 'default', 'other-batch', context())
        self.fails('docx_preview_exists', gate.preview_file, self.db, 'source', 'manual', 1, 'accepted', str(path), context())
        version_two = gate.preview_file(self.db, 'source', 'manual', 2, 'accepted', str(path), context())
        self.assertEqual(version_two['state'], 'ready')

    def test_reviewed_version_cannot_be_replaced_by_re_extraction(self):
        path = self._docx()
        first = gate.preview_file(self.db, 'source', 'manual', 1, 'accepted', str(path), context())
        gate.confirm(self.db, first['preview_id'], 'default', 'b1', context())
        other_view = gate.preview_file(self.db, 'source', 'manual', 1, 'original', str(path), context())
        self.fails('document_old_version', gate.confirm, self.db, other_view['preview_id'], 'default', 'b2', context())

    def test_revoked_authorization_leaves_only_failure_record(self):
        from corpus_gate.ingest.sources import revoke
        path = self._docx()
        revoke(self.db, 'source', 1, '撤回授权')
        preview = gate.preview_file(self.db, 'source', 'manual', 1, 'accepted', str(path), context())
        self.assertEqual(preview['state'], 'denied')
        self.assertIn('revoked', preview['authorization']['reasons'])
        self.fails('source_revoked', gate.confirm, self.db, preview['preview_id'], 'default', 'b', context())
        self.assertEqual(len(self.db.all('SELECT document_id FROM documents')), 0)
        row = self.db.one('SELECT state FROM docx_previews WHERE preview_id=?', (preview['preview_id'],))
        self.assertEqual(row['state'], 'denied')

    def test_unsupported_and_empty_previews_cannot_be_confirmed(self):
        broken = ds.build(ds.paragraph('前文') + '<w:p><w:r><w:altChunk r:id="x"/></w:r></w:p>')
        preview = gate.preview_file(self.db, 'source', 'broken', 1, 'accepted', str(broken), context())
        self.assertEqual(preview['state'], 'unsupported')
        self.fails('docx_unsupported', gate.confirm, self.db, preview['preview_id'], 'default', 'b', context())
        empty = ds.build('')
        empty_preview = gate.preview_file(self.db, 'source', 'empty-doc', 1, 'accepted', str(empty), context())
        self.assertEqual(empty_preview['state'], 'empty')
        self.fails('docx_empty', gate.confirm, self.db, empty_preview['preview_id'], 'default', 'b', context())

    def test_extraction_is_deterministic_and_tamper_evident(self):
        path = self._docx()
        first = gate.preview_file(self.db, 'source', 'manual', 1, 'accepted', str(path), context())
        second = gate.preview_file(self.db, 'source', 'manual', 1, 'accepted', str(path), context())
        self.assertEqual(first['preview_id'], second['preview_id'])
        self.assertEqual(first['result_digest'], second['result_digest'])
        self.db.execute("UPDATE docx_previews SET result=replace(result,'维护步骤','篡改文字') WHERE preview_id=?", (first['preview_id'],))
        self.fails('docx_preview_tampered', gate.confirm, self.db, first['preview_id'], 'default', 'b', context())

    def test_listing_and_rule_descriptor(self):
        path = self._docx()
        preview = gate.preview_file(self.db, 'source', 'manual', 1, 'accepted', str(path), context())
        listing = gate.list_previews(self.db)
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]['preview_id'], preview['preview_id'])
        descriptor = gate.rules()
        self.assertEqual(descriptor['supported_views'], ['accepted', 'original'])
        self.assertEqual(descriptor['numbering_formats'], ['bullet', 'decimal'])
        self.assertIn('word/footnotes.xml', descriptor['supported_parts'])

    def test_failed_confirm_rolls_back_without_consuming_preview(self):
        path = self._docx()
        preview = gate.preview_file(self.db, 'source', 'manual', 1, 'accepted', str(path), context())
        self.fails('profile_missing', gate.confirm, self.db, preview['preview_id'], 'missing-profile', 'b', context())
        self.assertEqual(len(self.db.all('SELECT batch_id FROM batches')), 0)
        self.assertEqual(gate.get_preview(self.db, preview['preview_id'])['state'], 'ready')
        confirmed = gate.confirm(self.db, preview['preview_id'], 'default', 'retry-batch', context())
        self.assertEqual(confirmed['state'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
