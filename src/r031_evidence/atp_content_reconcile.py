"""Read-only, exact source-cell reconciliation to historic R42 measurements."""
import hashlib
import json
import sqlite3
from pathlib import Path
from .atp_content_relations import verify_relations


def reconcile(science_db, science_sha, packets):
    db=Path(science_db).resolve()
    sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    if sha(db)!=science_sha:raise ValueError('ATP_SCIENCE_DB_SHA256_MISMATCH')
    index={};source_packets=[]
    for path in packets:
        d=json.loads(Path(path).read_text());verify_relations(d,d['source_path'])
        source_packets.append({'path':str(Path(path).resolve()),'sha256':sha(path)})
        for pt in d.get('native_numeric_points',[]):
            key=(d['paper']['pmcid'],d['source_sha256'],pt['table_id'],pt['cell_row'],pt['cell_column'])
            index.setdefault(key,[]).append(pt)
    c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    rows=c.execute('''SELECT m.*,t.table_id FROM r17_measurement m
        JOIN r17_experiment e USING(experiment_id) JOIN r16_source_table t USING(table_key)
        WHERE m.endpoint='ATP_CONTENT' ORDER BY m.paper_key,m.measurement_id''').fetchall()
    result=[];point_to_measurements={}
    for row in rows:
        # R16 appends a table ordinal; the original JATS ID is preserved before it.
        tableid=row['table_id'].rsplit(':',1)[0]
        key=(row['paper_key'],row['author_sha'],tableid,row['cell_row'],row['cell_column'])
        points=index.get(key,[])
        exact=[p for p in points if ' '.join(p['source_literal'].split())==' '.join(row['source_literal'].split()) and p['numeric_value']==row['numeric_value']]
        # Duplicate identical packets do not create a false ambiguity.
        exact={p['point_id']:p for p in exact}
        pt=next(iter(exact.values())) if len(exact)==1 else None
        state='EXACT_AUTHOR_CELL_AND_NUMERIC_REPLAY' if pt else 'NO_UNIQUE_EXACT_SOURCE_CELL_BINDING_HOLD'
        item={'measurement_id':row['measurement_id'],'experiment_id':row['experiment_id'],
            'source_cell_key':row['source_cell_key'],'original_locator':row['original_locator'],
            'paper':row['paper_key'],'author_sha256':row['author_sha'],'source_literal':row['source_literal'],
            'numeric_value':row['numeric_value'],'original_unit':row['unit_author_literal'],
            'point_id':pt['point_id'] if pt else None,'state':state,'science_approved':False}
        result.append(item)
        if pt:point_to_measurements.setdefault(pt['point_id'],[]).append(item)
    c.close()
    if sha(db)!=science_sha:raise ValueError('ATP_SCIENCE_DB_CHANGED_DURING_READONLY_RECONCILE')
    return {'schema':'asterloom.atp-content.native-measurement-reconciliation/1',
        'science_db':str(db),'science_sha256':science_sha,'source_packets':source_packets,
        'measurement_count':len(rows),'exact_replayed':sum(r['state']=='EXACT_AUTHOR_CELL_AND_NUMERIC_REPLAY' for r in result),
        'measurements':result,'point_to_measurements':point_to_measurements,
        'old_y_modified':False,'training_approved':False,
        'status':'EXACT_SOURCE_RECONCILIATION_NOT_SCIENTIFIC_OR_PROFILE_APPROVAL'}


def read_reconciliation(path, expected_sha):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha:raise ValueError('ATP_RECONCILIATION_SHA256_MISMATCH')
    data=json.loads(raw)
    rerun=reconcile(data['science_db'],data['science_sha256'],[x['path'] for x in data['source_packets']])
    if data!=rerun:raise ValueError('ATP_RECONCILIATION_REPLAY_MISMATCH')
    return data
