"""Read the existing SM297 parent registry without inventing tested identity."""
import csv
import hashlib
import json
from pathlib import Path
from .r39_targeting import registry


def load_parent_reference(manifest_path, expected_sha):
    raw=Path(manifest_path).read_bytes()
    if not expected_sha or hashlib.sha256(raw).hexdigest()!=expected_sha:
        raise ValueError('ATP_PARENT_REGISTRY_MANIFEST_SHA256_MISMATCH')
    manifest=json.loads(raw);data=registry(manifest['folder'])
    if data['file_SHA']!=manifest['file_SHA']:raise ValueError('ATP_PARENT_REGISTRY_FILE_SHA256_MISMATCH')
    structures={r['molecule_id']:r for r in csv.DictReader((Path(data['folder'])/'structures.csv').read_text().splitlines())}
    names={}
    for alias,values in data['names'].items():
        ids={v['molecule_id'] for v in values}
        if len(ids)!=1:continue
        v=values[0];s=structures[v['molecule_id']]
        names[alias]=dict(molecule_id='SM297:'+v['molecule_id'],preferred_name=v['name'].lower(),
            chemical_identity_alias=alias,natural_origin_scope=v['natural_state'],
            pubchem_cid=int(s['pubchem_cid']) if s['pubchem_cid'] else None,
            isomeric_smiles=s['standardized_smiles'],inchikey=s['inchi_key'],molecular_formula=s['molecular_formula'],
            pubchem_url=s['source_locator'],structure_status='FROZEN_SM297_PARENT_REFERENCE_SOURCE_ONLY',
            structure_rdkit_verified=0,chemical_form_caveat='Registry parent only; no experimental salt, lot, purity, or stereochemical form inferred.',
            technical_source_sha256=data['file_SHA']['structures.csv'])
    return names
