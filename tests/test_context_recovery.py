import importlib.util,json,socket,sys
from dataclasses import asdict,replace
from pathlib import Path
from r031_evidence.llm_harness import sha
from r031_evidence.llm_harness_gateway import ModelRequest
from r031_evidence.r44_arbiter import FIELDS


def test_thinking_is_explicit_in_exact_payload_identity(tmp_path):
    source=tmp_path/'author.xml';source.write_text('<article/>')
    req=ModelRequest('deepseek','deepseek-flash','PMC99999999',str(source),sha(source.read_bytes()),'s','p',{},dict(profile_id='FIXTURE'),'fixture','CC BY')
    thinking=replace(req,thinking_mode=True)
    assert json.loads(req.body())['thinking']['type']=='disabled'
    assert json.loads(thinking.body())['thinking']['type']=='enabled'
    assert json.loads(thinking.body())['reasoning_effort']=='high'
    assert thinking.request_sha256()!=req.request_sha256()


def test_invalid_later_correction_does_not_delete_valid_set(tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    from ast_scope_store_finalize import finalize
    source=tmp_path/'author.xml';source.write_text('<article><body><p>Actual source fixture for a review-only candidate.</p></body></article>')
    e=dict(paper='PMC99999999',split='PRIOR_EXPOSED_DEVELOPMENT_NOT_BLIND',source=dict(path=str(source),sha256=sha(source.read_bytes())),license_url='CC BY')
    inputs=tmp_path/'inputs.json';inputs.write_text(json.dumps(dict(rows=[e])))
    stage=tmp_path/'stage';folder=stage/e['paper'];folder.mkdir(parents=True)
    relation=dict(fields={k:None for k in FIELDS},evidence=[],uncertainties=[],scope=dict(author_experiment_refs=[],context_links=[]),comparison=None,measurements=[])
    answer=dict(paper=e['paper'],relations=[relation],reason_if_none=None,coverage_notes=[],complete=True)
    for r,content in [(1,json.dumps(answer)),(2,'{malformed')]:
        req=ModelRequest('deepseek','deepseek-flash',e['paper'],str(source),e['source']['sha256'],'s',str(r),{},dict(profile_id='SYNTHETIC'),'fixture','CC BY')
        (folder/f'REQUEST_{r}.json').write_text(json.dumps(asdict(req)))
        raw=json.dumps(dict(model='deepseek-flash',created=1794416400,choices=[dict(message=dict(content=content),finish_reason='stop')],usage=dict(prompt_tokens=100,completion_tokens=50,total_tokens=150))).encode()
        dst=stage/'gateway'/str(r);dst.mkdir(parents=True);(dst/'RAW_HTTP_RESPONSE.json').write_bytes(raw)
        result=dict(run_id=str(r),raw_response_sha256=sha(raw),answer=answer if r==1 else None,status='STRUCTURED_RESPONSE_REVIEW_ONLY' if r==1 else 'SCHEMA_HOLD_RAW_RETAINED',billing_mode='SYNTHETIC_OFFLINE_TEST')
        (folder/f'RESULT_{r}.json').write_text(json.dumps(result))
    unknown=replace(req,prompt='unknown third request')
    (folder/'REQUEST_3.json').write_text(json.dumps(asdict(unknown)))
    (folder/'RESULT_3.json').write_text(json.dumps(dict(run_id='3',raw_response_sha256=None,answer=None,status='NETWORK_UNKNOWN_HOLD',billing_mode='AUTHORIZED_REMOTE')))
    orig_connect,orig_method=socket.create_connection,socket.socket.connect
    try:s=finalize([inputs],[stage],tmp_path/'candidate.sqlite3',tmp_path/'out')
    finally:socket.create_connection=orig_connect;socket.socket.connect=orig_method
    assert s['proposals']==1 and s['recorded_response_runs']==2
    assert s['real_response_runs']==0 and s['simulated_response_runs']==2
    assert s['calculated_charge_usd']=='0'
    assert s['dispatch_attempts']==3 and len(s['unresolved_calls'])==1
    assert not s['total_charge_fully_known']
    import sqlite3
    with sqlite3.connect(tmp_path/'candidate.sqlite3') as conn:
        assert conn.execute('select count(*) from extraction_runs').fetchone()[0]==3
    assert s['papers'][0]['selected_run']['round']==1
    assert s['database_status']['chapter1_training_approved']==0
    assert s['foreign_key_check']==[] and s['finalization_new_api_calls']==0


def test_live_cli_default_off_including_large_output_flag(tmp_path):
    import subprocess
    script=Path(__file__).resolve().parents[1]/'scripts/ast_scope_store_live.py'
    result=subprocess.run([sys.executable,str(script),'--inputs',str(tmp_path/'absent.json'),'--out',str(tmp_path/'run'),'--ledger',str(tmp_path/'absent.sqlite3'),'--db',str(tmp_path/'candidate.sqlite3'),'--max-output-tokens','65536'],capture_output=True,text=True)
    assert result.returncode!=0 and 'LIVE_NETWORK_DEFAULT_OFF' in result.stderr
    assert not (tmp_path/'run').exists()
