"""Author-declared CID/formula corroboration against pinned official properties.

No name search, chemistry conversion, lot/purity claim or automatic approval.
Only exact author-reported name/CID/formula declarations are supported here.
"""
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from .atp_content_relations import _citation


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_references(path,expected_sha):
    if sha(path)!=expected_sha:raise ValueError('ATP_CHEMICAL_REFERENCE_MANIFEST_SHA_HOLD')
    data=json.loads(Path(path).read_text())
    if data.get('schema')!='asterloom.atp-content.chemical-reference-input/1':raise ValueError('ATP_CHEMICAL_REFERENCE_SCHEMA_HOLD')
    references={}
    for item in data['references']:
        cid=item['cid'];url=urlparse(item['url'])
        if type(cid) is not int or cid<1 or url.scheme!='https' or url.hostname!='pubchem.ncbi.nlm.nih.gov' or url.username or url.password or url.port or not url.path.startswith(f'/rest/pug/compound/cid/{cid}/property/'):
            raise ValueError('ATP_CHEMICAL_REFERENCE_OFFICIAL_CID_URL_REQUIRED')
        if sha(item['path'])!=item['sha256'] or sha(item['receipt_path'])!=item['receipt_sha256']:
            raise ValueError('ATP_CHEMICAL_REFERENCE_BYTES_SHA_HOLD')
        receipt=json.loads(Path(item['receipt_path']).read_text())
        if receipt.get('url')!=item['url'] or str(receipt.get('http_status'))!='200' or receipt.get('body_sha256')!=item['sha256']:
            raise ValueError('ATP_CHEMICAL_REFERENCE_RECEIPT_HOLD')
        props=json.loads(Path(item['path']).read_text())['PropertyTable']['Properties']
        if len(props)!=1 or props[0]['CID']!=cid or cid in references:raise ValueError('ATP_CHEMICAL_REFERENCE_UNIQUE_CID_REQUIRED')
        prop=props[0]
        if not re.fullmatch(r'[A-Z]{14}-[A-Z]{10}-[A-Z]',prop.get('InChIKey','')) or not prop.get('SMILES'):
            raise ValueError('ATP_CHEMICAL_REFERENCE_STRUCTURE_REQUIRED')
        references[cid]={'properties':prop,'reference':item}
    return references


def bind_author_identity(compound,methods,author_sha,references):
    matches=[]
    for b in methods:
        if not re.search(r'chemicals|reagents',' '.join(b['headings']),re.I):continue
        # A qualified salt/hydrate name cannot be shortened to its parent.
        if re.search(re.escape(compound)+r'\s+(?:hydrate|hydrochloride|sodium|potassium|salt|derivative)\b',b['text'],re.I):continue
        pattern=r'(?<!\w)(?P<name>'+re.escape(compound)+r')\s*\(\s*MF:\s*(?P<formula>[A-Za-z0-9]+);\s*MW:\s*(?P<mw>\d+(?:\.\d+)?)\s*g/mol;\s*PubChem\s+CID\s+(?P<cid>\d+)\s*\)\s+was\s+(?:obtained|purchased)\s+from'
        for m in re.finditer(pattern,b['text'],re.I):
            ref=references.get(int(m['cid']))
            if not ref:continue
            prop=ref['properties']
            if m['formula']!=prop['MolecularFormula'] or Decimal(m['mw'])!=Decimal(prop['MolecularWeight']):continue
            matches.append({'tested_form_literal':m['name'],'parent_id':'parent:'+prop['InChIKey'],
                'parent_structure':prop['SMILES'],'author_cid':int(m['cid']),'author_formula':m['formula'],
                'author_molecular_weight_literal':m['mw']+' g/mol','author_evidence':_citation(b,author_sha,m.group()),
                'official_properties':prop,'official_reference':ref['reference'],
                'scope':'AUTHOR_DECLARED_IDENTITY_AND_REFERENCE_PARENT_NOT_LOT_PURITY_OR_PHYSICAL_SPECIATION',
                'independent_scientific_approval':False})
    return matches[0] if len(matches)==1 else None
