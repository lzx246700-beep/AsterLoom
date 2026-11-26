"""Review model fields independently without promoting experimental relations."""
from __future__ import annotations
from .r44_arbiter import FIELDS, validate


def review(packet, answer):
    if not isinstance(answer,dict) or answer.get('paper') != packet['paper'] or not isinstance(answer.get('relations'),list):
        raise ValueError('FIELD_REVIEW_INVALID_MODEL_DOCUMENT')
    if len(answer['relations']) > 12: raise ValueError('FIELD_REVIEW_RELATION_BUDGET')
    results=[]
    for i, relation in enumerate(answer['relations']):
        if not isinstance(relation,dict) or not isinstance(relation.get('fields'),dict) or not isinstance(relation.get('evidence'),list):
            results.append({'index':i,'status':'SCHEMA_HOLD','accepted_fields':{},'rejected_fields':{}});continue
        accepted={}; rejected={}; provenance={}
        fields=relation['fields']
        for field in FIELDS:
            value=fields.get(field)
            if value is None:continue
            evidence=[e for e in relation['evidence'] if isinstance(e,dict) and e.get('field')==field]
            isolated={k:None for k in FIELDS}; isolated[field]=value
            singleton={'paper':packet['paper'],'relations':[{'fields':isolated,'evidence':evidence,'uncertainties':[]}], 'reason_if_none':None}
            try:
                check=validate(packet,singleton)['relations'][0]
                accepted[field]=value;provenance[field]=check['evidence']
            except (ValueError,KeyError,TypeError) as exc:
                code=str(exc).split(':',1)[0]
                rejected[field]=code if code.startswith('R44_') and len(code)<100 else type(exc).__name__
        results.append({'index':i,'status':'FIELD_SOURCE_CHECKED_RELATION_HOLD',
            'accepted_fields':accepted,'rejected_fields':rejected,'field_evidence':provenance,
            'scope_and_matching_verified':False,'independent_gold':0,'training_approved':0})
    return {'schema':'asterloom.field-source-review/0.1','paper':packet['paper'],
       'status':'MODEL_RELATIONS_REQUIRE_SCIENTIFIC_REVIEW','records':results,
       'scientific_accuracy_verified':False,'independent_gold':0,'training_approved':0}


def review_relations(packet, answer, *, drop_unsupported_fields=False):
    """Validate each unchanged relation; reject unknown top-level content.

    A missing reason_if_none is protocol metadata only when relations exist.
    No field, evidence, numerical value or group is reconstructed or modified.
    """
    if not isinstance(answer,dict) or set(answer)-{'paper','relations','reason_if_none'}:
        raise ValueError('RELATION_REVIEW_RESPONSE_SCHEMA_INVALID')
    if answer.get('paper')!=packet['paper'] or not isinstance(answer.get('relations'),list) or len(answer['relations'])>12:
        raise ValueError('RELATION_REVIEW_PAPER_OR_LIST_INVALID')
    if not answer['relations'] and not answer.get('reason_if_none'):
        raise ValueError('RELATION_REVIEW_EMPTY_REASON_REQUIRED')
    accepted=[]; rejected=[]
    for index, relation in enumerate(answer['relations']):
        single={'paper':answer['paper'],'relations':[relation],'reason_if_none':None}
        try:
            result=validate(packet,single)['relations'][0]
            result['review_index']=index
            accepted.append(result)
        except (ValueError,KeyError,TypeError) as exc:
            code=str(exc).split(':',1)[0]
            if drop_unsupported_fields:
                try:
                    if not isinstance(relation,dict) or set(relation)!={'fields','evidence','uncertainties'} or set(relation['fields'])!=set(FIELDS):raise ValueError('SANITIZE_INVALID_SHAPE')
                    if not isinstance(relation['evidence'],list) or any(not isinstance(e,dict) or set(e)!={'field','chunk_id','quote'} or e['field'] not in FIELDS for e in relation['evidence']):raise ValueError('SANITIZE_INVALID_EVIDENCE')
                    field_report=review(packet,{'paper':answer['paper'],'relations':[relation]})['records'][0]
                    dropped=field_report['rejected_fields']
                    if not dropped:raise ValueError('SANITIZE_NOT_APPLICABLE')
                    fields=dict(relation['fields'])
                    for field in dropped:fields[field]=None
                    if not fields.get('chemical_identity') or not fields.get('treatment') or not fields.get('endpoint'):raise ValueError('SANITIZE_REQUIRED_CORE_MISSING')
                    evidence=[e for e in relation['evidence'] if e['field'] not in dropped]
                    derived={'fields':fields,'evidence':evidence,'uncertainties':list(relation['uncertainties'])}
                    result=validate(packet,{'paper':answer['paper'],'relations':[derived],'reason_if_none':None})['relations'][0]
                    result['review_index']=index;result['sanitized_fields']=dropped
                    result['risk_flags']=sorted(set(result['risk_flags']+['UNSUPPORTED_MODEL_FIELDS_REMOVED_REVIEW_REQUIRED']))
                    accepted.append(result);continue
                except (ValueError,KeyError,TypeError):pass
            rejected.append({'index':index,'error':code if code.startswith('R44_') and len(code)<100 else type(exc).__name__})
    return {'schema':'asterloom.relation-source-review/1','paper':packet['paper'],
            'source_relation_count':len(answer['relations']),'relations':accepted,'rejected':rejected,
            'missing_reason_metadata_normalized':bool(answer['relations']) and 'reason_if_none' not in answer,
            'scientific_accuracy_verified':False,'independent_gold':0,'training_approved':0}
