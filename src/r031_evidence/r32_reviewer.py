"""Independent read-only ZIP reviewer. Stdlib only, no builder or database import.
Checks custody/anchors; never signs scientific validity or returns Gold labels.
"""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,math,re,stat,zipfile,xml.etree.ElementTree as ET

def sha(b):return hashlib.sha256(b).hexdigest()
def norm(s):return ' '.join(str(s).split())
def review(path,expected_manifest_sha=None):
 issues=[];checked=0;bound=0;unbound=0
 with zipfile.ZipFile(path) as z:
  info=z.infolist();names=[x.filename for x in info]
  if len(info)>101 or len(names)!=len(set(names)) or sum(x.file_size for x in info)>64000000:raise ValueError('R32_ARCHIVE_COUNT_OR_SIZE_OR_DUPLICATE')
  for x in info:
   p=PurePosixPath(x.filename)
   if p.is_absolute() or '..' in p.parts or '\\' in x.filename or stat.S_ISLNK(x.external_attr>>16):raise ValueError('R32_UNSAFE_ARCHIVE_MEMBER')
  b=z.read('MANIFEST.json');trusted=bool(expected_manifest_sha) and sha(b)==expected_manifest_sha
  if expected_manifest_sha and not trusted:raise ValueError('R32_MANIFEST_EXTERNAL_TRUST_ANCHOR_MISMATCH')
  m=json.loads(b)
  if m.get('schema')!='asterloom.evidence-bundle/1' or m.get('Gold')!=0:raise ValueError('R32_UNKNOWN_OR_PROMOTED_BUNDLE')
  if set(names)!=set(m['file_table'])|{'MANIFEST.json'}:raise ValueError('R32_UNLISTED_OR_MISSING_MEMBERS')
  blobs={}
  for n,r in m['file_table'].items():
   data=z.read(n)
   if len(data)!=r['bytes'] or sha(data)!=r['sha256']:raise ValueError('R32_ARTIFACT_SHA_OR_SIZE_MISMATCH:'+n)
   blobs[n]=data;checked+=1
  cells={};xmls={}
  for a in m['assets']:
   raw=blobs[a['source']]
   if sha(raw)!=a['source_sha256']:raise ValueError('R32_AUTHOR_SOURCE_SHA_MISMATCH')
   # Independent native permission/identity check, not trusting exporter flags.
   if b'<!ENTITY' in raw.upper():raise ValueError('R32_XML_ENTITY_DECLARATION_FORBIDDEN')
   root=ET.fromstring(raw);license_nodes=[e for e in root.iter() if e.tag.rsplit('}',1)[-1]=='license'];license_text=' '.join(' '.join(e.itertext())+' '+str(e.attrib) for e in license_nodes)
   links=' '.join(str(v) for e in license_nodes for node in e.iter() for v in node.attrib.values());license_text+=' '+links
   if not re.search(r'creativecommons\.org/licenses/by/|creative commons attribution(?:\s+[234]\.0\s+international)?\s+license',license_text,re.I) or re.search(r'by[-/]nc|by[-/]nd|noncommercial|non-commercial|no derivatives',license_text,re.I):raise ValueError('R32_INDEPENDENT_NATIVE_LICENSE_HOLD')
   dois=[norm(''.join(e.itertext())).lower() for e in root.iter() if e.tag.rsplit('}',1)[-1]=='article-id' and e.attrib.get('pub-id-type')=='doi']
   if a.get('doi') and a['doi'].lower() not in dois:raise ValueError('R32_INDEPENDENT_NATIVE_DOI_MISMATCH')
   ir=json.loads(blobs[a['portable_ir']])
   if ir.get('asset',{}).get('sha256')!=a['source_sha256']:raise ValueError('R32_IR_SOURCE_BINDING_MISMATCH')
   xmls[a['source_sha256']]=root
   for table in ir.get('tables',[]):
    for cell in table.get('cells',[]):
     anchor=cell.get('anchor',{});cells[(anchor.get('asset_sha256'),anchor.get('locator'))]=cell.get('raw_text')
  records=[json.loads(l) for l in blobs['observations.jsonl'].decode().splitlines() if l.strip()]
  if len(records)!=m['observation_count']:raise ValueError('R32_OBSERVATION_CARDINALITY_MISMATCH')
  for r in records:
   p=r['provenance'];key=(p['author_sha256'],p['original_locator']);literal=r['observation']['author_literal'];native=cells.get(key)
   if native is None:unbound+=1;issues.append('SOURCE_CELL_NOT_EMBEDDED_OR_NOT_SUPPORTED');continue
   if norm(native)!=norm(literal):raise ValueError('R32_LITERAL_TO_IR_ANCHOR_MISMATCH')
   # Check XML cell independently; no trust in builder/IR claimed flags.
   root=xmls[key[0]];loc=key[1];prefix='/'+root.tag.rsplit('}',1)[-1]+'/'
   node=root.find(loc[len(prefix):]) if loc.startswith(prefix) else None
   if node is None or norm(''.join(node.itertext()))!=norm(native):raise ValueError('R32_XML_TO_IR_ANCHOR_MISMATCH')
   match=re.match(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)',literal.replace(',',''))
   numeric=r['observation']['author_numeric']
   if match and isinstance(numeric,(int,float)) and not math.isclose(float(match[1]),numeric,rel_tol=1e-7,abs_tol=1e-9):raise ValueError('R32_NATIVE_NUMERIC_BINDING_MISMATCH')
   if r.get('task',{}).get('approved_training_y') is not None:raise ValueError('R32_UNAPPROVED_TRAINING_Y_PRESENT')
   bound+=1
  packet=json.loads(blobs['REVIEW_PACKET.json'])
  if packet.get('human_signature') is not None or packet.get('Gold')!=0:raise ValueError('R32_SCIENTIFIC_REVIEW_FORGED_OR_PROMOTED')
  if m.get('unbundled_external_reference_count'):issues.append('EXTERNAL_FIELD_ANCHORS_REQUIRE_SEPARATE_SOURCE_REVIEW')
 return dict(schema='asterloom.machine-evidence-review/1',status='INTEGRITY_VERIFIED_SCIENTIFIC_REVIEW_HOLD',externally_pinned_manifest=trusted,reviewer_implementation_sha256=sha(Path(__file__).read_bytes()),reviewer_kind='SEPARATE_PROCESS_DETERMINISTIC_CUSTODY_CHECK_NOT_HUMAN_SCI_REVIEW',files_verified=checked,source_assets=len(m['assets']),observations=len(records),native_cells_independently_bound=bound,unbound_observations=unbound,limitations=sorted(set(issues)),scientific_review_status='AWAITING_INDEPENDENT_SCIENTIFIC_REVIEW',reviewer_identity=None,human_signature=None,precision=None,recall=None,F1=None,Gold=0)

def main():
 p=argparse.ArgumentParser();p.add_argument('bundle');p.add_argument('--expected-manifest-sha');p.add_argument('--out');a=p.parse_args();d=review(a.bundle,a.expected_manifest_sha);s=json.dumps(d,ensure_ascii=False,indent=2)+'\n'
 if a.out:
  out=Path(a.out)
  with out.open('x') as f:f.write(s)
 print(s,end='')
if __name__=='__main__':main()
