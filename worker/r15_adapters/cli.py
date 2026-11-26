import argparse
import json
from dataclasses import asdict,is_dataclass
from pathlib import Path
from .transport import Transport
from .providers import discover,acquire_cloud
from .parsers import parse_jats,parse_pdf,parse_xlsx,parse_csv

def main():
    p=argparse.ArgumentParser(description='Source-only adapter; returns IR, never writes scientific DB')
    s=p.add_subparsers(dest='cmd',required=True)
    d=s.add_parser('discover');d.add_argument('--provider',choices=['openalex','europepmc','crossref'],required=True)
    d.add_argument('--query',required=True);d.add_argument('--cursor',default='*');d.add_argument('--limit',type=int,default=10);d.add_argument('--cache',required=True)
    a=s.add_parser('acquire');a.add_argument('--pmcid',required=True);a.add_argument('--version',type=int,default=1);a.add_argument('--cache',required=True);a.add_argument('--destination',required=True);a.add_argument('--pdf',action='store_true')
    r=s.add_parser('parse');r.add_argument('--input',required=True);r.add_argument('--paper-id',default='UNKNOWN');r.add_argument('--standard-pdf',action='store_true');r.add_argument('--models')
    for parser in [d,a,r]:parser.add_argument('--output',required=True)
    x=p.parse_args()
    if x.cmd=='discover': result=discover(Transport(x.cache),x.provider,x.query,x.cursor,x.limit)
    elif x.cmd=='acquire':result=acquire_cloud(Transport(x.cache),x.pmcid,x.destination,x.pdf,version=x.version)
    else:
        ext=Path(x.input).suffix.lower()
        if ext=='.pdf':result,_=parse_pdf(x.input,x.paper_id,x.standard_pdf,x.models)
        else:result={'.xml':parse_jats,'.xlsx':parse_xlsx,'.csv':parse_csv}[ext](x.input,x.paper_id)
    Path(x.output).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=lambda o:asdict(o) if is_dataclass(o) else str(o))+'\n')
    print(x.output)

if __name__=='__main__':main()
