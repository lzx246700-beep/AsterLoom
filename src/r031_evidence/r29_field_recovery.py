"""Source-anchored, append-only Contract field recovery in the existing Evidence Core.

An isolated schema832 extension; never changes native measurements or scientific
Profile standards. Packets are operator-reviewed extraction assertions, not SCI
reviews. Source bytes, exact XML nodes/IR cells and external identity references
are rechecked on every projection. Unknown author fields stay NULL.
"""
from __future__ import annotations
import hashlib
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

SCHEMA = 'asterloom.r29.field-recovery/1'
# Native Y, approval, split and biological-run identity are deliberately unwritable.
FIELDS = frozenset(('chemical.tested_form_literal', 'chemical.parent_structure',
    'chemical.parent_id', 'chemical.chemical_identity_class',
    'chemical.natural_provenance', 'chemical.tested_form_detail',
    'conditions.dose_literal', 'conditions.exposure_time_literal',
    'assay.platform', 'assay.algorithm_literal', 'assay.normalization_basis',
    'assay.method_locator', 'assay.independent_biological_n',
    'biological.model_literal', 'biological.model_kind',
    'biological.stimulus_literal', 'arm.control_linkage',
    'arm.control_verification_state'))
STATES = frozenset(('PRESENT', 'NOT_REPORTED', 'NOT_TESTED', 'NOT_MEASURED',
    'ACCESS_HOLD', 'UNVERIFIED_SOURCE', 'AMBIGUOUS', 'NOT_APPLICABLE'))
CAUSES = frozenset(('RECOVERED_EXTRACTOR_GAP', 'AUTHOR_REPORTING_GAP',
    'EXPERIMENT_DESIGN_MISMATCH', 'IDENTITY_AMBIGUITY', 'UNASSESSED',
    'SOURCE_ACCESS', 'EXTRACTOR_SEMANTIC_ERROR'))
DDL = (
    '''CREATE TABLE IF NOT EXISTS r29_recovery_packet (
       packet_sha TEXT PRIMARY KEY, packet_json TEXT NOT NULL,
       schema_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
    '''CREATE TABLE IF NOT EXISTS r29_field_recovery (
       packet_sha TEXT NOT NULL REFERENCES r29_recovery_packet(packet_sha),
       measurement_id TEXT NOT NULL REFERENCES r17_measurement(measurement_id),
       field_path TEXT NOT NULL, entry_json TEXT NOT NULL,
       PRIMARY KEY(measurement_id,field_path))''',
    '''CREATE TABLE IF NOT EXISTS r29_observation_hold (
       packet_sha TEXT NOT NULL REFERENCES r29_recovery_packet(packet_sha),
       measurement_id TEXT NOT NULL REFERENCES r17_measurement(measurement_id),
       reason TEXT NOT NULL, entry_json TEXT NOT NULL,
       PRIMARY KEY(measurement_id,reason))''')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def _norm(text):
    return ' '.join(text.split())

def _db(home):
    root = Path(home).expanduser().resolve()
    return (root if root.name == 'state' else root/'state')/'science.sqlite3'

def _readonly(db):
    c = sqlite3.connect('file:'+str(db)+'?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c

def _has(c):
    return c.execute("SELECT 1 FROM sqlite_master WHERE name='r29_recovery_packet'").fetchone() is not None

def _check_anchor(c, paper, anchor):
    path = Path(anchor['path']); expected = anchor['sha256']
    if not re.fullmatch('[0-9a-f]{64}', expected) or sha(path) != expected:
        raise ValueError('R29_SOURCE_SHA_DRIFT')
    kind = anchor['kind']
    if kind in ('AUTHOR_XML', 'AUTHOR_IR_CELL'):
        asset = c.execute('SELECT 1 FROM r16_parsed_asset WHERE paper_key=? AND original_sha=?',
                          (paper,expected)).fetchone()
        if not asset:
            raise ValueError('R29_AUTHOR_SOURCE_WRONG_STUDY_OR_NOT_REGISTERED')
        if kind == 'AUTHOR_XML':
            root = ET.parse(path).getroot()
            # Packet specifies an exact node, never a document-wide keyword match.
            nodes = root.findall(anchor['locator'])
            if len(nodes) != 1:
                raise ValueError('R29_XML_ANCHOR_NOT_UNIQUE')
            text = _norm(''.join(nodes[0].itertext()))
        else:
            rows = c.execute('''SELECT original_literal FROM r16_source_cell
                WHERE paper_key=? AND author_sha=? AND original_locator=?''',
                (paper,expected,anchor['locator'])).fetchall()
            if len(rows) != 1:
                raise ValueError('R29_IR_ANCHOR_NOT_UNIQUE')
            text = _norm(rows[0][0])
        if not anchor.get('quote') or _norm(anchor['quote']) not in text:
            raise ValueError('R29_EXACT_AUTHOR_QUOTE_NOT_IN_NODE')
        return text
    if kind == 'PUBCHEM_JSON':
        u=urlparse(anchor['url'])
        if u.scheme != 'https' or u.hostname != 'pubchem.ncbi.nlm.nih.gov' or not u.path.startswith('/rest/pug/compound/'):
            raise ValueError('R29_UNTRUSTED_CHEMICAL_RESOLVER')
        data=json.loads(path.read_text())['PropertyTable']['Properties']
        if len(data) != 1 or type(data[0]['CID']) is not int:
            raise ValueError('R29_AMBIGUOUS_CHEMICAL_RESOLVER')
        return data[0]
    raise ValueError('R29_ANCHOR_KIND_FORBIDDEN')

def _validate_entry(c, entry):
    native = c.execute('SELECT paper_key,author_sha,source_cell_key FROM r17_measurement WHERE measurement_id=?',
                       (entry['measurement_id'],)).fetchone()
    if not native or native['paper_key'] != entry['paper_key']:
        raise ValueError('R29_MEASUREMENT_STUDY_MISMATCH')
    anchors=entry.get('anchors',[])
    if not anchors or len(anchors)>12:
        raise ValueError('R29_EXACT_SOURCE_ANCHORS_REQUIRED')
    verified=[_check_anchor(c,entry['paper_key'],a) for a in anchors]
    authors=[(a,v) for a,v in zip(anchors,verified) if a['kind'].startswith('AUTHOR_')]
    if not authors:
        raise ValueError('R29_AUTHOR_ANCHOR_REQUIRED')
    if 'reason' in entry:
        if not re.fullmatch('[A-Z][A-Z0-9_]{3,150}', entry['reason']):
            raise ValueError('R29_HOLD_REASON_INVALID')
        return
    field=entry['field'];value=entry['value'];state=entry['state']
    if field not in FIELDS or state not in STATES or entry['cause'] not in CAUSES:
        raise ValueError('R29_FIELD_STATE_OR_CAUSE_FORBIDDEN')
    if state != 'PRESENT':
        if value is not None or not entry.get('note'):
            raise ValueError('R29_MISSING_FIELD_MUST_BE_NULL_WITH_SCOPE_NOTE')
        return
    if value is None or isinstance(value,(bool,dict,list)):
        raise ValueError('R29_PRESENT_TYPED_VALUE_REQUIRED')
    if field == 'assay.independent_biological_n':
        if type(value) is not int or value<1:
            raise ValueError('R29_BIOLOGICAL_N_INVALID')
    elif not isinstance(value,str) or not value:
        raise ValueError('R29_TEXT_FIELD_REQUIRED')
    rule=entry['rule']
    if field in ('chemical.parent_structure','chemical.parent_id') and rule != 'AUTHOR_NAME_OR_CID_TO_PUBCHEM':
        raise ValueError('R29_PARENT_REQUIRES_VERIFIED_REFERENCE_IDENTITY')
    if rule == 'EXACT_AUTHOR_LITERAL':
        if str(value) not in ' '.join(_norm(a['quote']) for a,_ in authors):
            raise ValueError('R29_VALUE_NOT_IN_AUTHOR_QUOTE')
    elif rule == 'SOURCE_SCOPED_CONTEXT_MAPPING':
        if not entry.get('note'):
            raise ValueError('R29_MAPPING_RATIONALE_REQUIRED')
    elif rule == 'AUTHOR_NAME_OR_CID_TO_PUBCHEM':
        if field not in ('chemical.parent_structure','chemical.parent_id'):
            raise ValueError('R29_IDENTITY_RULE_FIELD_MISMATCH')
        refs=[(a,v) for a,v in zip(anchors,verified) if a['kind']=='PUBCHEM_JSON']
        if len(refs)!=1:
            raise ValueError('R29_ONE_REFERENCE_IDENTITY_REQUIRED')
        a,prop=refs[0];identity=entry.get('identity',{})
        author_text=' '.join(_norm(x['quote']) for x,_ in authors)
        if identity.get('author_cid') is not None:
            if identity['author_cid'] != prop['CID'] or str(prop['CID']) not in author_text:
                raise ValueError('R29_AUTHOR_CID_MISMATCH')
        else:
            name=identity.get('exact_author_name','')
            # Case-only normalization, never an inferred shorthand/stereoisomer.
            requested=urlparse(a['url']).path.split('/name/',1)
            from urllib.parse import unquote
            if not name or name.casefold() not in author_text.casefold() or len(requested)!=2 or unquote(requested[1].split('/property/')[0]).casefold()!=name.casefold():
                raise ValueError('R29_AUTHOR_NAME_NOT_EXACT_RESOLVER_INPUT')
        expected=prop['SMILES'] if field.endswith('parent_structure') else 'parent:'+prop['InChIKey']
        if value != expected or not re.fullmatch('[A-Z]{14}-[A-Z]{10}-[A-Z]',prop['InChIKey']):
            raise ValueError('R29_REFERENCE_STRUCTURE_MISMATCH')
    else:
        raise ValueError('R29_RULE_NOT_IMPLEMENTED')
    if field == 'arm.control_linkage':
        other=c.execute('''SELECT paper_key,endpoint,unit_author_literal,experiment_id
            FROM r17_measurement WHERE measurement_id=?''',(value,)).fetchone()
        this=c.execute('''SELECT paper_key,endpoint,unit_author_literal,experiment_id
            FROM r17_measurement WHERE measurement_id=?''',(entry['measurement_id'],)).fetchone()
        if not other or tuple(other)!=tuple(this) or value==entry['measurement_id']:
            raise ValueError('R29_COMPARATOR_MUST_MATCH_STUDY_ASSAY_ENDPOINT_UNIT')

def apply(home, packet_file):
    db=_db(home);packet=json.loads(Path(packet_file).read_text());digest=sha(packet_file)
    if packet.get('schema') != SCHEMA:
        raise ValueError('R29_PACKET_SCHEMA_REQUIRED')
    with closing(_readonly(db)) as check:
        if check.execute('pragma user_version').fetchone()[0] != 832:
            raise ValueError('R29_ONLY_EXISTING_ISOLATED_SCHEMA832')
        if _has(check) and check.execute('SELECT 1 FROM r29_recovery_packet WHERE packet_sha=?',(digest,)).fetchone():
            # A retry still verifies bytes; idempotence must not hide source drift.
            for entry in packet['fields']+packet.get('holds',[]):_validate_entry(check,entry)
            return dict(status='R29_PACKET_ALREADY_APPLIED',packet_sha256=digest,Gold=0)
        if sha(db) != packet['expected_database_sha256']:
            raise ValueError('R29_DATABASE_BASELINE_DRIFT')
        for entry in packet['fields']+packet.get('holds',[]):_validate_entry(check,entry)
    c=sqlite3.connect(db);c.execute('pragma foreign_keys=on')
    try:
        c.execute('BEGIN IMMEDIATE')
        # Check the pinned byte snapshot again after obtaining the write lock.
        if sha(db) != packet['expected_database_sha256']:
            raise ValueError('R29_DATABASE_CHANGED_BEFORE_WRITE')
        before=c.execute('SELECT * FROM r17_measurement ORDER BY measurement_id').fetchall()
        for sql in DDL:c.execute(sql)
        for table in ('r29_recovery_packet','r29_field_recovery','r29_observation_hold'):
            for op in ('UPDATE','DELETE'):
                c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'R29_APPEND_ONLY'); END")
        c.execute('INSERT INTO r29_recovery_packet(packet_sha,packet_json,schema_id) VALUES(?,?,?)',
                  (digest,Path(packet_file).read_text(),SCHEMA))
        for entry in packet['fields']:
            c.execute('INSERT INTO r29_field_recovery VALUES(?,?,?,?)',
                      (digest,entry['measurement_id'],entry['field'],json.dumps(entry,sort_keys=True)))
        for entry in packet.get('holds',[]):
            c.execute('INSERT INTO r29_observation_hold VALUES(?,?,?,?)',
                      (digest,entry['measurement_id'],entry['reason'],json.dumps(entry,sort_keys=True)))
        assert before==c.execute('SELECT * FROM r17_measurement ORDER BY measurement_id').fetchall()
        if c.execute('pragma foreign_key_check').fetchall():raise ValueError('R29_FK_FAILURE')
        if c.execute('SELECT count(*) FROM qualified_training_labels').fetchone()[0]:raise ValueError('R29_GOLD_NOT_AUTHORIZED')
        c.commit()
    except BaseException:
        c.rollback();raise
    finally:c.close()
    return dict(status='R29_SOURCE_FIELD_RECOVERY_APPLIED',packet_sha256=digest,
                fields=len(packet['fields']),holds=len(packet.get('holds',[])),native_measurements_unchanged=True,Gold=0)

def enrich(projector,record):
    c=projector.c
    if not hasattr(projector,'has_r29_recovery'):projector.has_r29_recovery=_has(c)
    if not projector.has_r29_recovery:return record
    mid=record['observation']['native_measurement_id']
    fields={};anchors=[];holds=[]
    for row in c.execute('SELECT entry_json FROM r29_field_recovery WHERE measurement_id=? ORDER BY field_path',(mid,)):
        entry=json.loads(row[0]);field=entry['field'];part,key=field.split('.')
        try:
            _validate_entry(c,entry)
            record[part][key]=entry['value']
            if entry['state']=='PRESENT':
                locator=entry['anchors'][0]['locator']
                target={'conditions.dose_literal':('conditions','dose_source_locator'),
                    'conditions.exposure_time_literal':('conditions','time_source_locator'),
                    'biological.model_literal':('biological','model_source_locator'),
                    'chemical.tested_form_literal':('chemical','tested_form_source_locator')}.get(field)
                if target:record[target[0]][target[1]]=locator
            fields[field]=dict(state=entry['state'],cause=entry['cause'],note=entry.get('note'))
            anchors.append(dict(field=field,anchors=entry['anchors'],rule=entry['rule']))
        except (OSError,ValueError,KeyError,ET.ParseError):
            record[part][key]=None
            fields[field]=dict(state='UNVERIFIED_SOURCE',cause='SOURCE_ACCESS',note='Source drift or invalid anchor; field rejected')
            holds.append('R29_FIELD_SOURCE_VERIFICATION_FAILED')
    for row in c.execute('SELECT entry_json FROM r29_observation_hold WHERE measurement_id=? ORDER BY reason',(mid,)):
        entry=json.loads(row[0])
        try:_validate_entry(c,entry);holds.append(entry['reason'])
        except (OSError,ValueError,KeyError,ET.ParseError):holds.append('R29_HOLD_SOURCE_VERIFICATION_FAILED')
    if fields or holds:
        record['provenance']['r29_field_status']=fields
        record['provenance']['r29_verified_field_anchors']=anchors
        record['provenance']['r29_scientific_holds']=sorted(set(holds))
        if record['chemical'].get('parent_id'):
            record['chemical']['identity_scope']='REFERENCE_PARENT_NOT_SUPPLIER_LOT_PURITY_OR_HYDRATE_VERIFICATION'
    return record

def audit(home):
    from .r23_profiles import SourceProjector,NAMES,load,_f
    p=SourceProjector(home);views=[]
    try:
        for name in NAMES:
            plug=load(name,home);rows=list(p.project(plug))
            for v in plug.views:
                selected=[r for r in rows if r['view']==v['id']]
                ready=[r for r in selected if r['task']['decision']=='TASK_READY_CANDIDATE']
                views.append(dict(profile=name,view=v['id'],raw_observations=len(selected),
                    source_studies=len({r['dataset']['study_group'] for r in selected}),
                    profile_required_complete_unapproved_rows=len(ready),
                    profile_required_complete_unapproved_studies=len({r['dataset']['study_group'] for r in ready}),
                    native_source_excluded_rows=sum(bool(r['provenance'].get('r29_scientific_holds')) for r in selected),
                    fields={f:dict(present=sum(_f(r,f) is not None for r in selected),total=len(selected),
                        recovered=sum(r['provenance'].get('r29_field_status',{}).get(f,{}).get('state')=='PRESENT' for r in selected),
                        missing_causes={cause:sum(r['provenance'].get('r29_field_status',{}).get(f,{}).get('cause')==cause for r in selected) for cause in sorted(CAUSES)})
                        for f in v['required']+v.get('conditional',[])}))
    finally:p.close()
    return dict(schema='asterloom.r29.recovery-audit/1',views=views,Gold=0,precision=None,recall=None,F1=None,
                human_independent_reviewers=0,training_approved_studies=0,
                complete_candidate_is_not_reviewed_dataset=True)
