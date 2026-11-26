from r031_evidence.atp_content_author_identity import resolve_author_entity,name_pattern
from r031_evidence.atp_content_relations import _index,_body_paragraphs,extract_relations,verify_relations
from xml.etree import ElementTree as ET
from pathlib import Path
import re
import hashlib


def blocks(text):
    return _body_paragraphs(_index(ET.fromstring('<article><body><sec><p>'+text+'</p></sec></body></article>')))


import unittest

class AuthorIdentityTest(unittest.TestCase):
    def test_explicit_short_alias_only(self):
        r=resolve_author_entity('spermidine',blocks('Spermidine (SPD) was tested. MT was measured.'),'a'*64)
        assert 'SPD' in r['retrieval_names'] and 'MT' not in r['retrieval_names']
        assert r['declarations'][0]['evidence']['quote']=='Spermidine (SPD)'
        assert r['external_structure_identity']['external_id']=='pubchem:1102'
        assert r['external_structure_identity']['inchikey']=='ATHGHQPFGPMSJY-UHFFFAOYSA-N'
        assert not r['external_structure_identity']['identity_approved_for_training']

    def test_melatonin_author_abbreviation_maps_to_pinned_structure(self):
        r=resolve_author_entity('melatonin',blocks('Melatonin (MT) was applied to TM3 cells.'),'b'*64)
        assert 'MT' in r['retrieval_names']
        assert r['external_structure_identity']['external_id']=='pubchem:896'
        assert r['external_structure_identity']['inchikey']=='DRLFMBDRBRZALE-UHFFFAOYSA-N'
        assert not r['external_structure_identity']['identity_approved_for_training']

    def test_requested_profile_name_without_author_mention_is_not_source_identity(self):
        r=resolve_author_entity('melatonin',blocks('ATP was measured in TM3 cells.'),'c'*64)
        assert r['external_structure_identity'] is None
        assert not r['canonical_structure_verified']


    def test_locant_is_retrieval_not_structural_equivalence(self):
        r=resolve_author_entity('epigallocatechin gallate',blocks('Epigallocatechin-3-gallate (EGCG) was tested.'),'a'*64)
        assert 'EGCG' in r['retrieval_names']
        assert r['state']=='AUTHOR_AND_REQUESTED_NAMES_MATCH_PINNED_EXTERNAL_STRUCTURE'
        assert r['author_name']=='Epigallocatechin-3-gallate'
        assert r['external_structure_identity']['external_id']=='pubchem:65064'
        assert r['external_structure_identity']['inchikey']=='WMBWREPUVVBILR-WIYYLYMNSA-N'
        assert r['external_structure_identity']['canonical_structure_verified']
        assert not r['external_structure_identity']['identity_approved_for_training']

    def test_pterostilbene_keeps_stereo_hold_when_author_does_not_specify_e_or_z(self):
        r=resolve_author_entity('pterostilbene',blocks('Pterostilbene was provided in the diet.'),'a'*64)
        assert not r['canonical_structure_verified']
        assert r['external_structure_identity']['state']=='HOLD_REQUIRED_STEREOCHEMISTRY_NOT_IN_AUTHOR_SOURCE'
        assert r['external_structure_identity']['external_id_candidate']=='pubchem:5281727'






    def test_loaded_formulation_is_not_free_alias(self):
        r=resolve_author_entity('spermidine',blocks('Spermidine (SPD). RCEVs@SPD improved ATP.'),'a'*64)
        assert r['formulations'][0]['author_literal']=='RCEVs@SPD'
        assert not re.search(name_pattern(r['retrieval_names']),'RCEVs@SPD',re.I)
        assert not re.search(name_pattern(r['retrieval_names']),'SPD-loaded',re.I)

    def test_parent_named_nanotube_formulations_stay_distinct_from_free_alias(self):
        text=('Nicotinamide (NAM) was studied. Free NAM and NAM-PNTs were compared. '
              'The ordered formulations NAM-PNT0, NAM-PNT60, and NAM-PNT120 were tested.')
        r=resolve_author_entity('nicotinamide',blocks(text),'e'*64)
        forms={x['author_literal']:x for x in r['formulations']}
        self.assertEqual(set(forms),{'NAM-PNTs','NAM-PNT0','NAM-PNT60','NAM-PNT120'})
        self.assertTrue(all(x['state']=='AUTHOR_NAMED_FORMULATION_NOT_FREE_PARENT' for x in forms.values()))
        self.assertTrue(all(x['parent_author_alias']=='NAM' for x in forms.values()))
        self.assertTrue(all(x['evidence']['source_sha256']=='e'*64 for x in forms.values()))
        self.assertFalse(re.search(name_pattern(r['retrieval_names']),'NAM-PNT60',re.I))
        # Form recognition does not infer structure or approve identity/training.
        self.assertFalse(r['canonical_structure_verified'])
        self.assertFalse(r['training_approved'])



    def test_conflicting_short_alias_abstains(self):
        r=resolve_author_entity('melatonin',blocks('Melatonin (MT). microtubules (MT).'),'a'*64)
        assert 'MT' in r['ambiguous_abbreviations'] and 'MT' not in r['retrieval_names']
        # The full author name still supports identity; only the short alias is withheld.
        assert r['external_structure_identity']['external_id']=='pubchem:896'

    def test_repeated_author_definition_and_treatment_prose_do_not_create_alias_conflict(self):
        text=('Fucoxanthinol (FXL) was used. Fucoxanthinol (FXL) improved ATP. '
              'FXL cotreatment with chlorpyrifos changed ATP levels.')
        r=resolve_author_entity('fucoxanthinol',blocks(text),'d'*64)
        assert r['ambiguous_abbreviations']==[]
        assert 'FXL' in r['retrieval_names']

    def test_alias_conflict_requires_another_compact_name_definition(self):
        r=resolve_author_entity('fucoxanthinol',blocks('Fucoxanthinol (FXL). X-linked factor (FXL).'),'e'*64)
        assert 'FXL' in r['ambiguous_abbreviations']
        assert 'FXL' not in r['retrieval_names']

class SpacedAuthorAliasTest(unittest.TestCase):
    def test_spaced_alias_is_author_defined_not_inferred(self):
        r=resolve_author_entity('Schisandrin B',blocks('Schisandrin B (Sch B) was tested.'),'a'*64)
        self.assertIn('Sch B',r['retrieval_names'])
        self.assertEqual(r['declarations'][0]['evidence']['quote'],'Schisandrin B (Sch B)')
        absent=resolve_author_entity('Schisandrin B',blocks('Sch B was measured.'),'a'*64)
        self.assertNotIn('Sch B',absent['retrieval_names'])
    def test_spaced_alias_with_conflicting_author_definition_is_held(self):
        r=resolve_author_entity('Schisandrin B',blocks('Schisandrin B (Sch B). Alternative chemical (Sch B).'),'a'*64)
        self.assertIn('Sch B',r['ambiguous_abbreviations'])
        self.assertNotIn('Sch B',r['retrieval_names'])
