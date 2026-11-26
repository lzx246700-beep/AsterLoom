"""Rebuild an isolated review export and produce per-record evidence packets."""
from __future__ import annotations
import json,sqlite3,tempfile
from contextlib import closing
from pathlib import Path
from .scientific_review_export import export,sha


def replay(packet,packet_sha,source,model,model_sha,registry,registry_sha,rule,rule_sha,
           existing_db,existing_db_sha,out,*,drop_unsupported_fields=False,remap_unique_quotes=False):
    packet,source,model,registry,rule,existing_db,out=map(lambda p:Path(p).resolve(),(packet,source,model,registry,rule,existing_db,out))
    if out.exists() or any(out.is_relative_to(p.parent) for p in (packet,source,registry)):
        raise ValueError('REPLAY_NEW_ISOLATED_OUTPUT_REQUIRED')
    if sha(existing_db)!=existing_db_sha:raise ValueError('REPLAY_DATABASE_SHA_MISMATCH')
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.scientific-replay-',dir=out.parent) as scratch:
        temp=Path(scratch);recreated=temp/'recreated'
        result=export(packet,packet_sha,source,model,model_sha,registry,registry_sha,recreated,
          allow_isolated_relations=True,rule_packet_path=rule,rule_packet_sha=rule_sha,
          drop_unsupported_fields=drop_unsupported_fields,remap_unique_quotes=remap_unique_quotes)
        reproduced_sha=sha(recreated/'ATP_ENGINE_REVIEW_CANDIDATES.sqlite3')
        if reproduced_sha!=existing_db_sha:raise ValueError('REPLAY_DATABASE_CONTENT_DRIFT')
        with closing(sqlite3.connect(existing_db.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            if db.execute('pragma integrity_check').fetchone()[0]!='ok' or db.execute('pragma foreign_key_check').fetchall():raise ValueError('REPLAY_INTEGRITY_FAILURE')
            rows=[dict(r) for r in db.execute('select * from observations order by observation_id')]
        packets=[]
        for row in rows:
            if row['review_tier']!='Review-B' or row['independent_gold']!=0 or row['training_approved']!=0 or row['train_eligible']!=0:
                raise ValueError('REPLAY_UNEXPECTED_PROMOTION')
            locator=json.loads(row['source_locator']);packets.append({'schema':'asterloom.scientific-review-record/1',
              'observation_id':row['observation_id'],'paper_id':row['paper_id'],'author_source_path':str(source),
              'author_source_sha256':row['author_original_sha256'],'model_output_path':str(model),'model_output_sha256':model_sha,
              'rule_packet_path':str(rule),'rule_packet_sha256':rule_sha,'fields':{k:row[k] for k in ('cell_model','dose_original','exposure_original','treated_arm','comparator_arm','assay_method','endpoint_kind','measurement_unit','treated_value','control_value','author_reported_percent')},
              'field_evidence':locator.get('evidence',[]),'native_points':locator.get('native_points'),
              'sanitized_fields':locator.get('sanitized_fields',{}),'syntax_repair':locator.get('syntax_repair'),'citation_repairs':locator.get('citation_repairs',[]),'hold_reasons':json.loads(row['hold_reasons_json']),
              'scientific_review':'PENDING','independent_gold':0,'training_approved':0})
        (temp/'REVIEW_PACKETS.jsonl').write_text(''.join(json.dumps(p,ensure_ascii=False,sort_keys=True)+'\n' for p in packets))
        receipt={'schema':'asterloom.scientific-review-replay/1','status':'SOURCE_AND_DATABASE_REPLAY_PASSED_REVIEW_PENDING',
          'source_sha256':sha(source),'model_output_sha256':model_sha,'rule_packet_sha256':rule_sha,
          'existing_database_sha256':existing_db_sha,'reproduced_database_sha256':reproduced_sha,
          'record_count':len(rows),'native_numeric_pairs':result['native_numeric_pairs'],
          'evidence_packets_sha256':sha(temp/'REVIEW_PACKETS.jsonl'),'independent_gold':0,'training_approved':0}
        (temp/'REPLAY_RECEIPT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
        import shutil
        shutil.rmtree(recreated)
        temp.rename(out)
    return receipt
