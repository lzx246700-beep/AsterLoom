"""One-shot source-to-review pipeline with explicit partial and HOLD states."""
from __future__ import annotations
import json
from pathlib import Path
from .r44_models import Provider
from .scientific_extraction_harness import run as model_run, write_new
from .scientific_guided_rules import run as guided_run
from .scientific_review_export import export,sha
from .scientific_review_replay import replay


def finish(packet,packet_sha,source,model,model_sha,registry,registry_sha,out,*,drop_unsupported_fields=False,remap_unique_quotes=False):
    packet,source,model,registry,out=map(lambda p:Path(p).resolve(),(packet,source,model,registry,out))
    if out.exists() or any(out.is_relative_to(p.parent) for p in (packet,source,registry)):
        raise ValueError('PIPELINE_NEW_ISOLATED_OUTPUT_REQUIRED')
    if sha(registry)!=registry_sha or sha(model)!=model_sha:raise ValueError('PIPELINE_INPUT_SHA_MISMATCH')
    out.mkdir(parents=True,mode=0o700)
    try:
        guided=guided_run(packet,packet_sha,source,model,model_sha,out/'guided',drop_unsupported_fields=drop_unsupported_fields,remap_unique_quotes=remap_unique_quotes)
        rule=out/'guided/RULE_RELATIONS.json'
        dataset=export(packet,packet_sha,source,model,model_sha,registry,registry_sha,out/'three_table',
            allow_isolated_relations=True,rule_packet_path=rule,rule_packet_sha=sha(rule),
            drop_unsupported_fields=drop_unsupported_fields,remap_unique_quotes=remap_unique_quotes)
        if dataset['exported_observations']==0:raise ValueError('PIPELINE_NO_SOURCE_VERIFIED_RELATIONS')
        db=out/'three_table/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3'
        bundle=replay(packet,packet_sha,source,model,model_sha,registry,registry_sha,rule,sha(rule),db,sha(db),
                      out/'review_bundle',drop_unsupported_fields=drop_unsupported_fields,remap_unique_quotes=remap_unique_quotes)
        receipt={'schema':'asterloom.scientific-pipeline/0.1','status':'REVIEW_PACKETS_READY_SCIENCE_HOLD',
          'model_output_sha256':model_sha,'remap_unique_quotes':remap_unique_quotes,'rule_packet_sha256':sha(rule),'database_sha256':sha(db),
          'source_level_candidates':dataset['source_level_candidates'],
          'review_b_records':dataset['exported_observations'],'native_numeric_pairs':dataset['native_numeric_pairs'],
          'source_supported_numeric_relation_candidates':dataset['source_supported_numeric_relation_candidates'],
          'core_xy_fields_complete_unreviewed':dataset['core_xy_fields_complete_unreviewed'],
          'relation_validation_rejections':dataset['relation_validation_rejections'],
          'evidence_packets_sha256':bundle['evidence_packets_sha256'],
          'independent_gold':0,'training_approved':0}
    except (ValueError,KeyError,TypeError,RuntimeError) as exc:
        code=str(exc).split(':',1)[0]
        receipt={'schema':'asterloom.scientific-pipeline/0.1','status':'PIPELINE_HOLD',
          'reason':code if len(code)<100 and code.replace('_','').isalnum() else type(exc).__name__,
          'model_output_sha256':model_sha,'independent_gold':0,'training_approved':0}
    write_new(out/'PIPELINE_RECEIPT.json',receipt)
    return receipt


def run(packet,packet_sha,source,registry,registry_sha,out,*,model='deepseek-flash',
        strategy='scoped',drop_unsupported_fields=False,remap_unique_quotes=False,allow_network=False,
        allow_billed_remote=False,allow_source_disclosure=False):
    packet,source,registry,out=map(lambda p:Path(p).resolve(),(packet,source,registry,out))
    if not all((allow_network,allow_billed_remote,allow_source_disclosure)):
        raise ValueError('PIPELINE_EXPLICIT_NETWORK_BILLING_SOURCE_APPROVAL_REQUIRED')
    if out.exists() or any(out.is_relative_to(p.parent) for p in (packet,source,registry)):
        raise ValueError('PIPELINE_NEW_ISOLATED_OUTPUT_REQUIRED')
    if sha(registry)!=registry_sha:raise ValueError('PIPELINE_REGISTRY_SHA_MISMATCH')
    out.mkdir(parents=True,mode=0o700)
    try:
        model_result=model_run(packet,packet_sha,source,out/'model',Provider('deepseek',model),strategy=strategy,
          allow_network=allow_network,allow_billed_remote=allow_billed_remote,allow_source_disclosure=allow_source_disclosure)
        model_path=out/'model/MODEL_OUTPUT.json'
        if model_result['status']!='SOURCE_QUOTE_CHECKED_REVIEW_ONLY' and not drop_unsupported_fields:
            receipt={'schema':'asterloom.scientific-pipeline-run/0.1','status':'MODEL_VALIDATION_HOLD',
               'validation_error':model_result['validation_error'],'usage':model_result['usage'],
               'model_output_sha256':sha(model_path),'independent_gold':0,'training_approved':0}
        else:
            receipt=finish(packet,packet_sha,source,model_path,sha(model_path),registry,registry_sha,out/'review',drop_unsupported_fields=drop_unsupported_fields,remap_unique_quotes=remap_unique_quotes)
            receipt['usage']=model_result['usage']
    except (ValueError,KeyError,TypeError,RuntimeError) as exc:
        code=str(exc).split(':',1)[0]
        receipt={'schema':'asterloom.scientific-pipeline-run/0.1','status':'PIPELINE_HOLD',
          'reason':code if len(code)<100 and code.replace('_','').isalnum() else type(exc).__name__,
          'independent_gold':0,'training_approved':0}
    raw=out/'model/RAW_HTTP_RESPONSE.json'
    if raw.exists():
        receipt['raw_response_sha256']=sha(raw)
        try:
            usage=json.loads(raw.read_text()).get('usage') or {}
            receipt['usage']={k:v for k,v in usage.items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v>=0}
        except (ValueError,TypeError):receipt['usage']=None
    write_new(out/'RUN_RECEIPT.json',receipt);return receipt
