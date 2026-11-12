"""DOCX intake into Corpus Gate: source authorization, preview and confirmation.

The operator first extracts a raw .docx into a stored, tamper-evident preview,
explicitly choosing the ``accepted`` (revisions accepted) or ``original``
(before revisions) view.  The preview shows the body in document order with a
source-location mapping for every block.  Confirmation turns the preview into a
normal document record that continues through the existing text profile,
near-duplicate clustering and human review pipeline.

Re-extracting the same file never overwrites an already reviewed document
version: a newer document version is required.  When the source authorization
has been revoked, only a failure record is kept and no document is produced.
"""
import json
import pathlib

from corpus_gate import batch, documents
from corpus_gate.docx import package as docx_package
from corpus_gate.docx.rules import RULE_VERSION, SUPPORTED_VIEWS, extract, rule_descriptor
from corpus_gate.errors import canonical, digest, fail, identifier, integer, shape, text
from corpus_gate.ingest import sources

MAX_DOCX_BYTES = docx_package.MAX_PART_BYTES
RESULT_KEYS = ('view', 'rule', 'text', 'blocks', 'unsupported', 'ok', 'empty_text', 'footnotes', 'ignored_relationship_kinds')


def _preview_id(source_id, external_id, version, view, package_sha256, rule_version):
    return 'docx-' + digest([source_id, external_id, version, view, package_sha256, rule_version])


def read_file(path):
    source = pathlib.Path(path)
    if not source.is_file():
        fail('docx_file', 'DOCX file does not exist', path=path)
    if source.stat().st_size > MAX_DOCX_BYTES:
        fail('docx_package_size', 'DOCX file exceeds the size limit')
    return source.name, source.read_bytes()


def _stored(db, preview_id):
    row = db.one('SELECT * FROM docx_previews WHERE preview_id=?', (preview_id,))
    if row is None:
        fail('docx_preview_missing', 'Extraction preview does not exist')
    for key in ('result', 'evidence', 'authorization'):
        row[key] = json.loads(row[key])
    return row


def _result_digest(view, rule_version, result):
    return digest({'view': view, 'rule_version': rule_version, 'text': result['text'], 'blocks': result['blocks']})


def _persist_result(result):
    return {key: result[key] for key in RESULT_KEYS}


def preview(db, source_id, external_id, version, view, file_name, raw, context):
    identifier(source_id, 'source_id')
    identifier(external_id, 'external_id')
    integer(version, 'version', 1, 1000000000)
    text(file_name, 'file_name', 1024)
    if view not in SUPPORTED_VIEWS:
        fail('docx_view', 'View must be accepted or original', view=view)
    shape(context, {'purpose', 'region', 'at', 'fulfilled_obligations'})
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        fail('docx_bytes', 'DOCX input must be nonempty bytes')
    sources.get(db, source_id)
    authorization = sources.authorize(db, source_id, context)
    package = docx_package.load(raw)
    result = extract(package, view)
    package_sha = package['evidence']['package_sha256']
    preview_id = _preview_id(source_id, external_id, version, view, package_sha, RULE_VERSION)
    result_digest = _result_digest(view, RULE_VERSION, result)
    existing = db.one('SELECT state FROM docx_previews WHERE preview_id=?', (preview_id,))
    if existing and existing['state'] == 'confirmed':
        fail('docx_preview_exists', 'This exact extraction was already confirmed; re-extract under a new version')
    if not authorization['allowed']:
        state = 'denied'
    elif result['unsupported']:
        state = 'unsupported'
    elif result['empty_text']:
        state = 'empty'
    else:
        state = 'ready'
    with db.transaction():
        db.execute(
            'INSERT INTO docx_previews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(preview_id) DO UPDATE SET state=excluded.state,result=excluded.result,result_digest=excluded.result_digest,evidence=excluded.evidence,authorization=excluded.authorization',
            (preview_id, source_id, external_id, version, view, state, package_sha, RULE_VERSION, result_digest, None, None,
             canonical(_persist_result(result)), canonical(package['evidence']), canonical(authorization), file_name, db.clock(), None),
        )
        db.log('docx.previewed', {'preview_id': preview_id, 'source_id': source_id, 'external_id': external_id, 'version': version, 'view': view, 'state': state, 'package_sha256': package_sha, 'rule_version': RULE_VERSION, 'authorization_allowed': authorization['allowed'], 'reasons': authorization['reasons']})
    return _preview_report(preview_id, source_id, external_id, version, view, state, file_name, package_sha, result_digest, result, package['evidence'], authorization)


def _preview_report(preview_id, source_id, external_id, version, view, state, file_name, package_sha, result_digest, result, evidence, authorization):
    return {
        'preview_id': preview_id,
        'source_id': source_id,
        'external_id': external_id,
        'version': version,
        'view': view,
        'state': state,
        'file_name': file_name,
        'package_sha256': package_sha,
        'rule': result['rule'],
        'authorization': authorization,
        'evidence': evidence,
        'text': result['text'],
        'blocks': result['blocks'],
        'unsupported': result['unsupported'],
        'footnotes': result['footnotes'],
        'result_digest': result_digest,
    }


def get_preview(db, preview_id):
    row = _stored(db, preview_id)
    return _preview_report(row['preview_id'], row['source_id'], row['external_id'], row['version'], row['view'], row['state'], row['file_name'], row['package_sha256'], row['result_digest'], row['result'], row['evidence'], row['authorization'])


def confirm(db, preview_id, profile_id, batch_id, context):
    shape(context, {'purpose', 'region', 'at', 'fulfilled_obligations'})
    with db.transaction():
        row = _stored(db, preview_id)
        result = row['result']
        if _result_digest(row['view'], row['rule_version'], result) != row['result_digest']:
            fail('docx_preview_tampered', 'Stored extraction no longer matches its digest')
        if row['state'] == 'confirmed':
            fail('docx_already_confirmed', 'Preview was already confirmed', document_id=row['document_id'])
        if row['state'] == 'denied':
            fail('source_revoked', 'Source authorization was withdrawn; only the failure record remains')
        if row['state'] == 'unsupported':
            fail('docx_unsupported', 'Extraction contains unsupported structures', unsupported=result['unsupported'])
        if row['state'] == 'empty':
            fail('docx_empty', 'Extraction produced no body text and cannot be admitted')
        authorization = sources.authorize(db, row['source_id'], context)
        if not authorization['allowed']:
            db.execute("UPDATE docx_previews SET state='denied' WHERE preview_id=?", (preview_id,))
            db.log('docx.confirm_denied', {'preview_id': preview_id, 'reasons': authorization['reasons']})
            fail('source_revoked', 'Source authorization was withdrawn; only the failure record remains', reasons=authorization['reasons'])
        head = documents.head(db, row['source_id'], row['external_id'])
        if head and row['version'] <= head['version']:
            fail('document_old_version', 'A reviewed or current document version already exists; choose a higher version for this extraction')
        metadata = {
            'origin': 'docx',
            'file_name': row['file_name'],
            'package_sha256': row['package_sha256'],
            'view': row['view'],
            'rule_version': row['rule_version'],
            'preview_id': preview_id,
            'source_locations': [block['location'] for block in result['blocks']],
            'mapping_digest': digest(result['blocks']),
        }
        record = {'external_id': row['external_id'], 'version': row['version'], 'text': result['text'], 'metadata': metadata}
        record_digest = digest(record)
        staged = batch.stage(db, batch_id, row['source_id'], profile_id, [record])
        batch.process(db, batch_id, staged['revision'], 1)
        intake_row = next(item for item in batch.records(db, batch_id) if item['ordinal'] == 0)
        if intake_row['state'] != 'processed':
            fail('docx_intake_failed', 'Confirmed extraction failed document intake', error=intake_row['error'])
        document_id = intake_row['document_id']
        db.execute('UPDATE docx_previews SET state=?,record_digest=?,document_id=?,confirmed_at=? WHERE preview_id=?', ('confirmed', record_digest, document_id, db.clock(), preview_id))
        db.changed()
        db.log('docx.confirmed', {'preview_id': preview_id, 'batch_id': batch_id, 'document_id': document_id, 'record_digest': record_digest})
        return {'preview_id': preview_id, 'state': 'confirmed', 'batch_id': batch_id, 'document_id': document_id, 'record_digest': record_digest, 'document_version': row['version'], 'view': row['view']}


def compare(db, left_preview_id, right_preview_id):
    left = _stored(db, left_preview_id)
    right = _stored(db, right_preview_id)
    left_locations = [block['location'] for block in left['result']['blocks']]
    right_locations = [block['location'] for block in right['result']['blocks']]
    return {
        'left_preview_id': left_preview_id,
        'right_preview_id': right_preview_id,
        'same_source_file': left['package_sha256'] == right['package_sha256'],
        'view_changed': left['view'] != right['view'],
        'views': {'left': left['view'], 'right': right['view']},
        'rule_version_changed': left['rule_version'] != right['rule_version'],
        'rule_versions': {'left': left['rule_version'], 'right': right['rule_version']},
        'text_changed': left['result']['text'] != right['result']['text'],
        'location_mapping_changed': left_locations != right_locations,
        'left_state': left['state'],
        'right_state': right['state'],
        'text_digests': {'left': left['result_digest'], 'right': right['result_digest']},
    }


def list_previews(db, source_id=None, external_id=None):
    query = 'SELECT preview_id FROM docx_previews WHERE 1=1'
    args = []
    if source_id is not None:
        query += ' AND source_id=?'
        args.append(identifier(source_id))
    if external_id is not None:
        query += ' AND external_id=?'
        args.append(identifier(external_id))
    query += ' ORDER BY created_at,preview_id'
    result = []
    for row in db.all(query, args):
        stored = _stored(db, row['preview_id'])
        result.append({'preview_id': stored['preview_id'], 'source_id': stored['source_id'], 'external_id': stored['external_id'], 'version': stored['version'], 'view': stored['view'], 'state': stored['state'], 'file_name': stored['file_name'], 'package_sha256': stored['package_sha256'], 'rule_version': stored['rule_version'], 'document_id': stored['document_id'], 'created_at': stored['created_at'], 'confirmed_at': stored['confirmed_at']})
    return result


def rules():
    return rule_descriptor()


def preview_file(db, source_id, external_id, version, view, path, context):
    file_name, raw = read_file(path)
    return preview(db, source_id, external_id, version, view, file_name, raw, context)
