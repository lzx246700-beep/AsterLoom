import json,sqlite3
from r031_evidence.llm_harness import sha
from r031_evidence.llm_harness_relations import context_source_packet
from r031_evidence.scientific_scope_store import normalize_candidate,ingest_candidates
from r031_evidence.auto_silver import qualify,store
from r031_evidence.r44_arbiter import FIELDS
from r031_evidence.ch1_training_views import install_views

def test_source_bound_view_preserves_evidence_and_separates_endpoint(tmp_path):
    text='Compound X increased ATP content by 19% versus control. ATP assay normalized to protein.'
    source=tmp_path/'author.xml';source.write_text('<article><body><sec><title>Results</title><p>'+text+'</p></sec></body></article>')
    packet=context_source_packet(dict(paper='PMC99999999',split='PRIOR_EXPOSED_DEVELOPMENT_NOT_BLIND',source=dict(path=str(source),sha256=sha(source.read_bytes()))));ch=packet['chunks'][0]
    fields={k:None for k in FIELDS};fields.update(treatment='Compound X',chemical_identity='Compound X',comparator='control',assay_method='ATP assay',endpoint='ATP_CONTENT',direction='INCREASE',value='19%')
    raw=dict(fields=fields,evidence=[dict(field=k,quote=text,chunk_id=ch['chunk_id']) for k,v in fields.items() if v],scope={},comparison=dict(chunk_id=ch['chunk_id'],quote=text),measurements=[])
    candidate=normalize_candidate(packet,raw,proposal_id='one',run=dict(raw_response_sha256='a'*64))
    candidate['scope']['quality_audit']=dict(comparison_role='MATCHED_CONTROL',effect_kind='PERCENT_CHANGE',verdict='SUPPORTED',issues=[])
    db=tmp_path/'candidate.sqlite3';ingest_candidates({packet['paper']:packet},[candidate],db);store(db,qualify([candidate],{packet['paper']:packet}));install_views(db)
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
        row=conn.execute('select endpoint,y_type,effect_value,molecule_name,dose_original,field_evidence_json from chapter1_silver_content_numeric').fetchone()
        assert row[:5]==('ATP_CONTENT','PERCENT_CHANGE',19,'Compound X',None)
        evidence=json.loads(row[5]);assert any(e['quote']==text and e['asset_sha256']==packet['source_sha256'] for e in evidence)
        assert conn.execute('select count(*) from chapter1_silver_rate_numeric').fetchone()[0]==0
        assert conn.execute('pragma foreign_key_check').fetchall()==[]
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    from ast_ch1_training_export import run
    result=run(db,tmp_path/'export')
    assert result['statistics']['numeric_papers']==1
    assert result['statistics']['numeric_y_types']=={'PERCENT_CHANGE':1}
    assert result['statistics']['historical_run_costs']['missing_cost_runs']==1
    assert result['statistics']['historical_run_costs']['cost_per_label_usd'] is None
