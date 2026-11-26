"""Use source-checked model names to run existing deterministic author extraction."""
from __future__ import annotations
import json
from pathlib import Path
from .r44_packets import verify_packet
from .r44_field_review import review_relations
from .atp_content_relations import extract_relations, verify_relations
from .scientific_extraction_harness import replay_packet,write_new
from .scientific_review_export import sha


def run(packet_path,packet_sha,source_path,model_path,model_sha,out,*,drop_unsupported_fields=False,remap_unique_quotes=False):
    packet_path,source_path,model_path,out=map(lambda x:Path(x).resolve(),(packet_path,source_path,model_path,out))
    if out.exists():raise ValueError('GUIDED_OUTPUT_MUST_BE_NEW')
    if out in source_path.parents or out in packet_path.parents or out.is_relative_to(source_path.parent) or out.is_relative_to(packet_path.parent) or any(x in str(out) for x in ('phase1b_r42/production_home','phase1b_r43/full_replay_home','state/current')):
        raise ValueError('GUIDED_PROTECTED_OUTPUT')
    if sha(model_path)!=model_sha:raise ValueError('GUIDED_MODEL_SHA_MISMATCH')
    packet=verify_packet(packet_path,packet_sha);replay=replay_packet(packet,source_path)
    model=json.loads(model_path.read_text());journal=model_path.parent
    request=json.loads((journal/'REQUEST.json').read_text());result=json.loads((journal/'RESULT.json').read_text());raw=json.loads((journal/'RAW_HTTP_RESPONSE.json').read_text())
    if (request.get('packet_sha256')!=packet_sha or result.get('model_output_sha256')!=model_sha or
        raw.get('model')!=model.get('model')):
        raise ValueError('GUIDED_MODEL_JOURNAL_MISMATCH')
    from .scientific_json_repair import verify_journal_answer
    syntax_repair=verify_journal_answer(journal,raw,model)
    if remap_unique_quotes:
        from .scientific_quote_remap import review_with_remap
        checked=review_with_remap(packet,model['answer'],source_path,drop_unsupported_fields=drop_unsupported_fields)
    else:
        checked=review_relations(packet,model['answer'],drop_unsupported_fields=drop_unsupported_fields)
    names=sorted({row['fields']['chemical_identity'] for row in checked['relations'] if row['fields']['chemical_identity']})
    if not names or len(names)>12 or any(len(n)>100 for n in names):raise ValueError('GUIDED_SOURCE_NAMES_INVALID_OR_EMPTY')
    data=extract_relations(source_path,packet['source_sha256'],names);rule_replay=verify_relations(data,source_path)
    out.mkdir(parents=True,mode=0o700)
    rule_sha=write_new(out/'RULE_RELATIONS.json',data)
    receipt={'schema':'asterloom.model-guided-rule-replay/1','status':'REVIEW_ONLY',
             'source_packet_sha256':packet_sha,'source_replay':replay,'model_output_sha256':model_sha,
             'source_quoted_names':names,'model_rejected_relations':checked['rejected'],
             'drop_unsupported_fields':drop_unsupported_fields,'remap_unique_quotes':remap_unique_quotes,'syntax_repair':syntax_repair,
             'rule_packet_sha256':rule_sha,'rule_replay':rule_replay,'rule_candidate_count':len(data['candidates']),
             'independent_gold':0,'training_approved':0}
    write_new(out/'RECEIPT.json',receipt)
    return receipt
