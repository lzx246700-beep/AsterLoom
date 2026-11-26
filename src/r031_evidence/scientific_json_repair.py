"""Review-only recovery of exactly one extra JSON structural delimiter.

Only an unambiguous deletion outside strings is considered. Model values and
quotes are never generated, altered or interpreted by the syntax repair.
"""
from __future__ import annotations
import hashlib,json,os
from pathlib import Path
from .r44_packets import verify_packet
from .scientific_extraction_harness import replay_packet,write_new
from .r44_arbiter import validate


def _sha(data): return hashlib.sha256(data).hexdigest()

def repair_one_extra_delimiter(content):
    if not isinstance(content,str) or len(content)>30000:raise ValueError('JSON_REPAIR_CONTENT_BUDGET')
    try:json.loads(content)
    except json.JSONDecodeError:pass
    else:raise ValueError('JSON_REPAIR_INPUT_ALREADY_VALID')
    inside=False;escape=False;positions=[]
    for i,ch in enumerate(content):
        if inside:
            if escape:escape=False
            elif ch=='\\':escape=True
            elif ch=='"':inside=False
        elif ch=='"':inside=True
        elif ch in '{}[],':positions.append(i)
    if len(positions)>2048:raise ValueError('JSON_REPAIR_DELIMITER_BUDGET')
    hits=[]
    for i in positions:
        candidate=content[:i]+content[i+1:]
        try:answer=json.loads(candidate)
        except (json.JSONDecodeError,RecursionError):continue
        if isinstance(answer,dict):hits.append((i,candidate,answer))
        if len(hits)>1:raise ValueError('JSON_REPAIR_AMBIGUOUS')
    if len(hits)!=1:raise ValueError('JSON_REPAIR_NOT_UNIQUE')
    index,fixed,answer=hits[0]
    receipt={'schema':'asterloom.json-syntax-repair/1','operation':'DELETE_ONE_EXTRA_STRUCTURAL_DELIMITER_OUTSIDE_STRING',
      'position':index,'deleted_character':content[index],'original_content_sha256':_sha(content.encode()),
      'repaired_content_sha256':_sha(fixed.encode()),'candidate_count':1,
      'model_values_modified':False,'scientific_approval':False}
    return answer,receipt


def verify_journal_answer(journal,raw,model):
    content=raw['choices'][0]['message']['content'];receipt_path=Path(journal)/'SYNTAX_REPAIR.json'
    if receipt_path.exists():
        answer,expected=repair_one_extra_delimiter(content)
        result=json.loads((Path(journal)/'RESULT.json').read_text())
        if result.get('syntax_repair_sha256')!=_sha(receipt_path.read_bytes()):raise ValueError('JSON_REPAIR_RESULT_HASH_MISMATCH')
        if json.loads(receipt_path.read_text())!=expected:raise ValueError('JSON_REPAIR_RECEIPT_MISMATCH')
        if answer!=model.get('answer'):raise ValueError('JSON_REPAIR_ANSWER_MISMATCH')
        return expected
    if json.loads(content)!=model.get('answer'):raise ValueError('MODEL_RAW_JOURNAL_MISMATCH')
    return None


def derive(packet_path,packet_sha,source_path,original_journal,out):
    packet_path,source_path,original_journal,out=map(lambda x:Path(x).resolve(),(packet_path,source_path,original_journal,out))
    if out.exists() or any(out.is_relative_to(p) for p in (packet_path.parent,source_path.parent,original_journal)):
        raise ValueError('JSON_REPAIR_NEW_ISOLATED_OUTPUT_REQUIRED')
    if any(x in str(out) for x in ('phase1b_r42/production_home','phase1b_r43/full_replay_home','state/current')):raise ValueError('JSON_REPAIR_PROTECTED_OUTPUT')
    packet=verify_packet(packet_path,packet_sha);replay=replay_packet(packet,source_path)
    request_raw=(original_journal/'REQUEST.json').read_bytes();request=json.loads(request_raw)
    raw_bytes=(original_journal/'RAW_HTTP_RESPONSE.json').read_bytes();raw=json.loads(raw_bytes)
    if request.get('packet_sha256')!=packet_sha or request.get('source_replay',{}).get('source_sha256')!=packet['source_sha256'] or raw.get('model')!=request.get('model'):
        raise ValueError('JSON_REPAIR_ORIGINAL_JOURNAL_MISMATCH')
    answer,repair=repair_one_extra_delimiter(raw['choices'][0]['message']['content'])
    usage=raw.get('usage') or {};clean={k:v for k,v in usage.items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v>=0}
    out.mkdir(parents=True,mode=0o700)
    for name,data in (('REQUEST.json',request_raw),('RAW_HTTP_RESPONSE.json',raw_bytes)):
        with (out/name).open('xb') as f:os.fchmod(f.fileno(),0o600);f.write(data);f.flush();os.fsync(f.fileno())
    write_new(out/'SYNTAX_REPAIR.json',repair)
    model={'answer':answer,'provider':request['provider'],'model':raw['model'],'usage':clean,
           'auth':'DERIVED_FROM_IMMUTABLE_RAW_RESPONSE','is_fixture':False}
    model_sha=write_new(out/'MODEL_OUTPUT.json',model)
    try:checked=validate(packet,answer);status='SOURCE_QUOTE_CHECKED_REVIEW_ONLY';error=None
    except (ValueError,KeyError,TypeError) as e:checked=None;status='VALIDATION_HOLD';error=str(e).split(':')[0]
    result={'schema':'asterloom.scientific-extraction-result/0.1','status':status,'validation_error':error,
      'model_output_sha256':model_sha,'source_replay':replay,'usage':clean,'checked_candidates':checked,
      'syntax_repair_sha256':_sha((out/'SYNTAX_REPAIR.json').read_bytes()),'independent_gold':0,'training_approved':0}
    write_new(out/'RESULT.json',result)
    return {'status':status,'validation_error':error,'model_output_sha256':model_sha,'repair':repair,'usage':clean,'independent_gold':0,'training_approved':0}
