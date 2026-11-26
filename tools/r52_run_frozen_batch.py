"""Run installed CLI on <=20 pinned local originals; never fetch or label data."""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--manifest',required=True);p.add_argument('--manifest-sha',required=True)
    p.add_argument('--python',required=True);p.add_argument('--out',required=True)
    p.add_argument('--figure-packet',action='append',default=[])
    a=p.parse_args();manifest=Path(a.manifest).resolve()
    if sha(manifest)!=a.manifest_sha:raise ValueError('FROZEN_MANIFEST_SHA_MISMATCH')
    data=json.loads(manifest.read_text());cases=data['cases']
    if not 1<=len(cases)<=20 or len({c['paper'] for c in cases})!=len(cases):
        raise ValueError('BOUNDED_UNIQUE_SOURCE_BATCH_REQUIRED')
    for c in cases:
        if sha(c['source_path'])!=c['source_sha256']:raise ValueError('AUTHOR_SHA_MISMATCH')
    out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=False)
    commands=[]
    def run(args, label):
        argv=[str(Path(a.python).absolute()),'-I','-m','r031_evidence.r21_product',*args]
        t=time.monotonic();r=subprocess.run(argv,capture_output=True,text=True,timeout=90,
                                             env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'})
        stdout=out/(label+'.stdout.json');stderr=out/(label+'.stderr.log')
        stdout.write_text(r.stdout);stderr.write_text(r.stderr)
        receipt={'argv':argv,'exit_code':r.returncode,'elapsed_s':time.monotonic()-t,
                 'stdout_sha256':sha(stdout),'stderr_sha256':sha(stderr)}
        commands.append(receipt);(out/'COMMAND_RECEIPTS.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2)+'\n')
        if r.returncode:raise RuntimeError(label+' failed; inspect saved stdout/stderr')
    for c in cases:
        args=['--home',str(out/'unused_offline_home'),'atp-relations','--source',c['source_path'],
              '--expected-sha',c['source_sha256'],'--out',str(out/(c['paper']+'.relations.json'))]
        for compound in c['compounds']:args.extend(['--compound',compound])
        run(args,c['paper'])
    args=['atp-export','--registry-db',data['reference_db'],'--registry-sha',data['reference_db_sha256'],
          '--out',str(out/'three_table_candidates_v001')]
    for c in cases:args.extend(['--packet',str(out/(c['paper']+'.relations.json'))])
    for packet in a.figure_packet:args.extend(['--figure-packet',str(Path(packet).resolve())])
    run(args,'EXPORT')
    summary={'schema':'asterloom.r52.actual-offline-batch-receipt/1',
        'manifest_sha256':a.manifest_sha,'network_requests':0,'model_api_calls':0,
        'source_cases':len(cases),'commands':len(commands),'results':[],
        'science_precision':None,'science_recall':None,'science_f1':None,'human_gold':0}
    for c in cases:
        d=json.loads((out/(c['paper']+'.relations.json')).read_text())
        summary['results'].append({'paper':c['paper'],'candidates':len(d['candidates']),
            'candidate_fields_bound':{k:sum(v['fields'][k]['value'] is not None for v in d['candidates'])
                for k in ('cell_model','dose_uM','pretreatment_h','challenge_h','comparator_arm','assay_method','normalization')},
            'relation_packet_sha256':sha(out/(c['paper']+'.relations.json'))})
    summary['export']=json.loads((out/'three_table_candidates_v001/MANIFEST.json').read_text())
    (out/'BATCH_RECEIPT.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
