"""Offline per-relation Scientific Harness risk audit, preserving partial fields.

Legacy block co-location flags are reported as legacy diagnostics, not a new
same-paragraph requirement. Verified context links remain available for review.
"""
import re
from . import r44_arbiter


def audit(packet,candidate):
    f=candidate['fields'];raw=candidate['original'];flags=[];legacy=[];legacy_error=None
    ev=[{k:e[k] for k in ('field','chunk_id','quote')} for e in candidate['evidence'] if f.get(e['field']) is not None]
    try:
        answer=dict(paper=packet['paper'],relations=[dict(fields=f,evidence=ev,uncertainties=raw.get('uncertainties',[]))],reason_if_none=None)
        checked=r44_arbiter.validate(packet,answer)['relations'][0]
        legacy=checked['risk_flags']
    except ValueError as exc:legacy_error=str(exc)
    from .scientific_scope_store import canonical_quantity
    if f.get('cell_model') and re.search(r'(?i)\b(?:and|or)\b|[,;]',f['cell_model']):flags.append('MULTIPLE_CELL_MODELS_REQUIRE_SPLIT_REVIEW')
    if f.get('dose') and canonical_quantity(f['dose'],'concentration') is None:flags.append('DOSE_UNIT_OR_RANGE_NOT_MOLAR_SCALAR')
    if f.get('exposure_time') and canonical_quantity(f['exposure_time'],'time') is None:flags.append('EXPOSURE_UNIT_OR_RANGE_NOT_HOUR_SCALAR')
    # Author endpoint language, not a model's interpretation, drives this hold.
    endpoint_text=' '.join(e['quote'] for e in candidate['evidence'] if e['field'] in ('endpoint','assay_method','normalization'))
    source_index={x['chunk_id']:x for x in packet['chunks']}
    method_source=' '.join(source_index[e['chunk_id']]['text'] for e in candidate['evidence'] if e['field']=='assay_method' and e['chunk_id'] in source_index)
    calibrated=bool(re.search(r'(?is)ATP.*standard curve|standard curve.*ATP',method_source))
    per_cell=bool(re.search(r'(?i)normaliz\w* to cell number|normaliz\w* against total protein',method_source))
    if calibrated or per_cell:
        # normalize_candidate records a preliminary kit-name warning. Once the
        # cited author protocol supplies calibration/normalization, retire it.
        candidate['holds']=[h for h in candidate['holds'] if h!='VIABILITY_PROXY_ENDPOINT_REVIEW']
    if f.get('endpoint')=='ATP_CONTENT':
        if not calibrated and not per_cell and re.search(r'(?i)viability|cell survival|cell proliferation|cell.titer.glo',endpoint_text):
            flags.append('VIABILITY_PROXY_ENDPOINT_REVIEW')
        if re.search(r'(?i)ATP.linked respiration|oxygen consumption|\bOCR\b',endpoint_text):
            flags.append('ATP_CONTENT_VS_RESPIRATION_REVIEW')
        if not calibrated and not per_cell and re.search(r'(?i)ATP production',endpoint_text):
            flags.append('ATP_PRODUCTION_TERMINOLOGY_METHOD_REVIEW')
    # Existing source-centric flags are useful but two legacy paragraph-wide
    # rules would duplicate the known same-block/multiple-value limitation.
    flags+=['SCIENTIFIC_'+x for x in legacy if x not in ('TREATMENT_COMPARATOR_NOT_SAME_SOURCE_BLOCK','MULTI_ARM_EFFECT_MUST_BE_SPLIT') and not (x in ('CELLTITER_GLO_VIABILITY_SIGNAL_ENDPOINT_REVIEW','ATP_PRODUCTION_TERMINOLOGY_REQUIRES_METHOD_REVIEW') and (calibrated or per_cell))]
    if legacy_error:flags.append('LEGACY_STRICT_VERIFIER_HOLD')
    result=dict(per_relation_verifier='r44_arbiter.validate(normalized-supported-fields)',
        legacy_flags=legacy,legacy_error=legacy_error,author_calibrated_pool_method=calibrated,author_per_cell_or_protein_normalization=per_cell,scientific_flags=sorted(set(flags)),
        source_scope_verifier=candidate['scope'],independent_scientific_approval=False)
    # Avoid recursive inclusion: only the prior source scope is captured here.
    candidate['scope']=dict(candidate['scope'],scientific_audit=result)
    candidate['holds']=sorted(set(candidate['holds']+flags))
    return candidate
