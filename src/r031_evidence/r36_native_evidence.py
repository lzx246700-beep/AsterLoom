"""Native author structure preview, no new scientific values or image estimates.
OOXML literals and JATS spans stay source-bound; formulas are never executed.
"""
from pathlib import Path,PurePosixPath
import collections,io,posixpath,re,zipfile,xml.etree.ElementTree as ET
from .r30_ledger import digest

def xml(raw):
 if len(raw)>20000000 or b'<!ENTITY' in raw.upper() or b'<!DOCTYPE' in raw.upper() and b'[' in raw[:4096]:raise ValueError('R36_XML_ENTITY_OR_SIZE_HOLD')
 return ET.fromstring(raw)
def tag(e):return e.tag.rsplit('}',1)[-1] if isinstance(e.tag,str) else ''
def raw_text(e):return ''.join(e.itertext()) if e is not None else ''
def shared_string_parts(si):
 """Keep ruby phonetic runs out of the author base text. Do not treat them as a measurement."""
 base=[]; phonetic=[]
 def walk(node, in_rph):
  if tag(node)=='t' and node.text:
   (phonetic if in_rph else base).append(node.text)
  for child in node:
   walk(child, in_rph or tag(child)=='rPh')
 walk(si, False)
 return ''.join(base), ''.join(phonetic)
def literal(e):return raw_text(e).strip()
def ooxml_evidence_view(cell):
 """Separate author text, formula text and cached value. Nothing here is executed or promoted to a measurement.

 String-form differences, including the 23 still-unconfirmed representations, stay unclassified.
 """
 retention=cell.get("retention")
 formula=cell.get("formula")
 cached=cell.get("literal") if retention=="FORMULA_CACHED_NOT_A_MEASUREMENT" else None
 return {"schema":"asterloom.ooxml-cell-evidence/1","retention":retention,
         "author_text":cell.get("raw_text"),"stripped_text":cell.get("literal") if retention=="NONBLANK_LITERAL" else None,
         "phonetic_text":cell.get("phonetic"),"formula_text":formula,"cached_value":cached,"formula_executed":False,
         "phonetic_is_new_measurement":False,
         "cached_value_is_new_measurement":False,"string_normalization":"UNCONFIRMED_NOT_A_MEASUREMENT",
         "counts_as_assay_measurement":False}

def ooxml_retention(raw, has_formula, cache_present):
 """Classify an OOXML cell without executing a formula or treating it as a measurement."""
 text=raw or ''
 if has_formula:
  return 'FORMULA_CACHED_NOT_A_MEASUREMENT' if cache_present and text.strip() else 'FORMULA_CACHE_MISSING_HOLD'
 if text.strip():return 'NONBLANK_LITERAL'
 if text:return 'WHITESPACE_ONLY'
 return 'EMPTY'
def xpath_map(root):
 paths={id(root):'/'+tag(root)}
 def visit(e):
  totals=collections.Counter(tag(c) for c in e);seen=collections.Counter()
  for c in e:
   name=tag(c);seen[name]+=1;paths[id(c)]=paths[id(e)]+'/'+name+('['+str(seen[name])+']' if totals[name]>1 else '');visit(c)
 visit(root);return paths

def native_jats(raw):
 root=xml(raw);paths=xpath_map(root);parents={id(c):e for e in root.iter() for c in e};out=[]
 for table in root.iter():
  if tag(table)!='table':continue
  wrap=parents.get(id(table));cells=[];occupied=set();maxcol=0
  for row,tr in enumerate((e for e in table.iter() if tag(e)=='tr'),1):
   col=1
   for td in tr:
    if tag(td) not in ('th','td'):continue
    while (row,col) in occupied:col+=1
    rs=int(td.attrib.get('rowspan','1'));cs=int(td.attrib.get('colspan','1'))
    if not 1<=rs<=100 or not 1<=cs<=100 or len(cells)>100000:raise ValueError('R36_TABLE_SPAN_OR_CELL_BUDGET')
    for rr in range(row,row+rs):
     for cc in range(col,col+cs):
      if (rr,cc) in occupied:raise ValueError('R36_OVERLAPPING_AUTHOR_SPANS')
      occupied.add((rr,cc))
    cells.append(dict(row=row,column=col,rowspan=rs,colspan=cs,literal=literal(td),locator=paths[id(td)],role='HEADER' if tag(td)=='th' or tag(parents.get(id(tr),tr))=='thead' else 'BODY'));maxcol=max(maxcol,col+cs-1);col+=cs
  notes=[literal(e) for e in (wrap.iter() if wrap is not None else []) if tag(e)=='table-wrap-foot'];caps=[literal(e) for e in (wrap if wrap is not None else table) if tag(e)=='caption']
  out.append(dict(table_id=(wrap.attrib.get('id') if wrap is not None else None) or paths[id(table)],locator=paths[id(table)],caption=' '.join(caps),footnotes=notes,cells=cells,logical_rows=max((x['row']+x['rowspan']-1 for x in cells),default=0),logical_columns=maxcol))
 return out

def native_xlsx(raw):
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  infos=z.infolist();names=[i.filename for i in infos]
  if len(names)!=len(set(names)) or len(names)>3000 or sum(i.file_size for i in infos)>50000000 or any('..' in PurePosixPath(n).parts or PurePosixPath(n).is_absolute() or 'vbaproject' in n.lower() for n in names):raise ValueError('R36_OOXML_ARCHIVE_HOLD')
  shared=[]
  if 'xl/sharedStrings.xml' in names:shared=[shared_string_parts(e) for e in xml(z.read('xl/sharedStrings.xml')) if tag(e)=='si']
  rels={e.attrib['Id']:e.attrib['Target'] for e in xml(z.read('xl/_rels/workbook.xml.rels')) if e.attrib.get('TargetMode')!='External'};out=[];total=0
  for sheet in xml(z.read('xl/workbook.xml')).iter():
   if tag(sheet)!='sheet':continue
   rid=next((v for k,v in sheet.attrib.items() if k.rsplit('}',1)[-1]=='id'),None);target=rels.get(rid)
   if not target:continue
   target=posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/'+target)
   if not target.startswith('xl/') or target not in names:raise ValueError('R36_WORKSHEET_TARGET_HOLD')
   doc=xml(z.read(target));cells=[];merges=[]
   for e in doc.iter():
    if tag(e)=='mergeCell':merges.append(e.attrib['ref'])
    if tag(e)!='c':continue
    a1=e.attrib.get('r','');m=re.fullmatch('([A-Z]{1,3})([1-9][0-9]{0,6})',a1)
    if not m:raise ValueError('R36_OOXML_CELL_ADDRESS_HOLD')
    vnode=next((c for c in e if tag(c)=='v'),None);val=vnode.text or '' if vnode is not None else '';kind=e.attrib.get('t','n');fnode=next((c for c in e if tag(c)=='f'),None);formula=fnode.text if fnode is not None else None;has_formula=fnode is not None;cache_present=vnode is not None and vnode.text not in (None,'');formula_state=('AUTHOR_CACHED_VALUE_NOT_RECALCULATED' if cache_present else 'FORMULA_CACHE_MISSING_HOLD') if has_formula else None
    phonetic=None
    if kind=='s' and val:
     raw,phonetic=shared[int(val)]
    elif kind=='inlineStr':
     pieces=[shared_string_parts(c) for c in e if tag(c)=='is']
     raw=''.join(part[0] for part in pieces); phonetic=''.join(part[1] for part in pieces) or None
    else:raw=val
    val=raw.strip();retention=ooxml_retention(raw,has_formula,cache_present)
    col=0
    for char in m[1]:col=col*26+ord(char)-64
    cells.append(dict(row=int(m[2]),column=col,rowspan=1,colspan=1,literal=val,raw_text=raw,phonetic=phonetic or None,retention=retention,locator="sheet='"+sheet.attrib['name'].replace("'","''")+"'!"+a1,role='AUTHOR_CELL',a1=a1,source_type=kind,formula=formula,formula_state=formula_state));total+=1
    if total>100000:raise ValueError('R36_OOXML_CELL_BUDGET')
   out.append(dict(table_id=sheet.attrib['name'],locator="sheet='"+sheet.attrib['name']+"'",caption=sheet.attrib['name'],footnotes=[],merged_ranges=merges,cells=cells,logical_rows=max((x['row'] for x in cells),default=0),logical_columns=max((x['column'] for x in cells),default=0)))
  return out

def tables(path,sha):
 p=Path(path);raw=p.read_bytes()
 if len(raw)>20000000 or digest(raw)!=sha:raise ValueError('R36_ORIGINAL_SHA_OR_SIZE_HOLD')
 suffix=p.suffix.lower()
 if suffix=='.xlsx':out=native_xlsx(raw)
 elif suffix in ('.xml','.nxml'):out=native_jats(raw)
 else:return dict(state='NATIVE_PREVIEW_UNSUPPORTED',tables=[],sha256=sha)
 return dict(state='AUTHOR_NATIVE_STRUCTURE_ONLY',tables=out,sha256=sha,scientific_measurements_created=0,image_estimates_created=0,Gold=0)

def preview(path,sha,locator=None):
 d=tables(path,sha);lines=[];focus=[]
 for t in d['tables']:
  hits=[c for c in t['cells'] if c['locator']==locator]
  if hits:focus.append((t,hits[0]['row']))
 chosen=focus or [(t,None) for t in d['tables'][:3]];shown=0
 for t,row in chosen:
  lines.append('TABLE/SHEET '+t['table_id']+'  '+t['caption'])
  if t.get('merged_ranges'):lines.append('AUTHOR MERGED RANGES '+', '.join(t['merged_ranges']))
  for c in t['cells']:
   if row is not None and c['row']>4 and abs(c['row']-row)>6:continue
   if shown>=400:break
   lines.append(('▶ ' if c['locator']==locator else '')+f"R{c['row']}C{c['column']} [{c['role']}; span {c['rowspan']}×{c['colspan']}] "+c['literal']+'\n  '+c['locator']+(' · '+c['formula_state'] if c.get('formula_state') else ''));shown+=1
  for note in t['footnotes']:lines.append('AUTHOR FOOTNOTE '+note)
 if Path(path).suffix.lower() in ('.xml','.nxml'):
  root=xml(Path(path).read_bytes());paths=xpath_map(root)
  if locator:
   rel=locator[len('/'+tag(root)+'/'):] if locator.startswith('/'+tag(root)+'/') else locator
   try:node=root.find(rel)
   except (SyntaxError,KeyError):node=None
   if node is not None:lines.insert(0,'AUTHOR EXACT SOURCE NODE '+locator+'\n'+literal(node)+'\n')
  # Methods are original context, never automatically assigned to a treatment.
  shown_methods=set();method_text=[]
  for sec in root.iter():
   if tag(sec)!='sec':continue
   title=' '.join(literal(e) for e in sec if tag(e)=='title')
   if not re.search(r'method|material',title,re.I):continue
   for e in sec.iter():
    if tag(e)=='p' and id(e) not in shown_methods:
     shown_methods.add(id(e));method_text.append(paths[id(e)]+'\n'+literal(e))
  if method_text:lines.append('AUTHOR METHODS CONTEXT — ASSAY LINK REQUIRES REVIEW\n'+'\n\n'.join(method_text)[:60000])
 lines.append('原始字面值与结构预览；不自动继承单位或生成实验关系。')
 return dict(state=d['state'],sha256=sha,text='\n'.join(lines)[:200000],shown_cells=shown,source_tables=len(d['tables']),preview_truncated=shown>=400,download_supported=True)
