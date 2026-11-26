"""Narrow, auditable repairs of non-scientific JSON metadata only."""
from copy import deepcopy
from .llm_harness import digest

def normalize(answer, coverage):
    data=deepcopy(answer);changes=[]
    if not isinstance(data,dict):return answer,[]
    relations=data.get('relations')
    if isinstance(relations,list) and relations and isinstance(coverage,dict) and coverage.get('coverage_complete') is False:
        defaults={'complete':False,'coverage_notes':['PROGRAM_ADDED: source coverage incomplete; see coverage manifest'], 'reason_if_none':None}
        for key,value in defaults.items():
            if key not in data:
                data[key]=value;changes.append(dict(path=key,reason='PROGRAM_ADDED_NONSCIENTIFIC_METADATA_FROM_COVERAGE',value=value))
    if isinstance(relations,list):
        for i,rel in enumerate(relations):
            if not isinstance(rel,dict) or not isinstance(rel.get('evidence'),list):continue
            for j,ev in enumerate(rel['evidence']):
                if isinstance(ev,dict) and 'field_label' in ev and ev['field_label'] is None:
                    del ev['field_label'];changes.append(dict(path=f'relations.{i}.evidence.{j}.field_label',reason='REMOVED_EXTRA_NULL_FIELD',value=None))
                if isinstance(ev,dict) and 'value' in ev:
                    value=ev['value'];fields=rel.get('fields') or {}
                    if isinstance(value,str) and value and value==fields.get('value') and value in (ev.get('quote') or ''):
                        del ev['value'];changes.append(dict(path=f'relations.{i}.evidence.{j}.value',reason='DUPLICATE_OF_RELATION_VALUE_AND_QUOTE',value=value))
    return data,changes

def receipt(original,normalized,changes,raw_sha):
    return dict(raw_response_sha256=raw_sha,original_json_sha256=digest(original),normalized_json_sha256=digest(normalized),changes=changes)
