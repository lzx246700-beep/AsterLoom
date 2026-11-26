import hashlib
import io
import json
import sqlite3
import unittest
import zipfile
from pathlib import Path
from unittest import mock
import test_atp_content_export as fixture
from r031_evidence.atp_content_build import build


class Build(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.Export();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.root=self.fx.root;self.manifest=self.root/'manifest.json'
        self.data={'cases':[{'paper':'PMC000','source_path':str(self.fx.case.p),
            'source_sha256':hashlib.sha256(self.fx.case.p.read_bytes()).hexdigest(),'compounds':['typhaneoside']}]}
        self.manifest.write_text(json.dumps(self.data));self.hash=hashlib.sha256(self.manifest.read_bytes()).hexdigest()

    def run_build(self,resume=False):
        return build(self.manifest,self.hash,self.fx.registry,self.fx.digest,self.root/'build',resume=resume)

    def add_verified_supplement(self, permission_status='AUTHOR_DECLARED_CC_BY_4_0_VERIFIED'):
        source=self.fx.case.p
        import test_atp_content_relations as relation_fixture
        raw=relation_fixture.BODY
        raw=raw.replace('</body>', '<supplementary-material id="sup"><media href="source.docx"/>'
                        '</supplementary-material></body>')
        raw=raw.replace('</article-meta>', '<permissions><license href="https://creativecommons.org/licenses/by/4.0/">'
                        '<p>Creative Commons Attribution 4.0</p></license></permissions></article-meta>')
        source.write_text(raw)
        source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
        blob=io.BytesIO()
        document=b'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:r><w:t>ATP Assay</w:t></w:r></w:p>
        <w:p><w:r><w:t>Intracellular ATP levels were measured using a chemiluminescence assay.</w:t></w:r></w:p>
        </w:body></w:document>'''
        with zipfile.ZipFile(blob,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('word/document.xml',document)
        path=self.root/'source.docx';path.write_bytes(blob.getvalue())
        self.data['cases'][0]['source_sha256']=source_sha
        self.data['cases'][0]['supplementary_sources']=[{
            'source_path':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'parent_source_sha256':source_sha,'declared_filename':'source.docx',
            'native_supplement_id':'unnamed-native-supplement','native_supplement_href':'source.docx',
            'permission_status':permission_status,
            'official_source_url':'https://publisher.example/source.docx','http_status':200,
        }]
        self.manifest.write_text(json.dumps(self.data));self.hash=hashlib.sha256(self.manifest.read_bytes()).hexdigest()

    def test_end_to_end_and_explicit_resume_do_not_duplicate(self):
        first=self.run_build();db=self.root/'build/three_table_candidates_v001/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3'
        h=hashlib.sha256(db.read_bytes()).hexdigest();second=self.run_build(True)
        self.assertEqual(first,second);self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(),h)
        self.assertEqual(first['counts']['observations'],1)
        self.assertEqual(first['counts']['Train-A'],0)

    def test_interruption_after_source_packet_can_resume_without_rewrite(self):
        with mock.patch('r031_evidence.atp_content_build.export_candidates',side_effect=RuntimeError('injected stop')):
            with self.assertRaisesRegex(RuntimeError,'injected stop'):self.run_build()
        p=self.root/'build/relations/PMC000.json';h=hashlib.sha256(p.read_bytes()).hexdigest()
        state=json.loads((self.root/'build/BUILD_STATE.json').read_text());self.assertEqual(state['status'],'HOLD')
        self.run_build(True);self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),h)

    def test_resume_modified_artifact_is_rejected(self):
        self.run_build();p=self.root/'build/relations/PMC000.json';p.write_text(p.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'ARTIFACT_SHA256'):self.run_build(True)

    def test_resume_changed_identity_reference_is_rejected(self):
        self.run_build()
        import sqlite3
        c=sqlite3.connect(self.fx.registry);c.execute("update molecules set chemical_form_caveat='changed'");c.commit();c.close()
        with self.assertRaisesRegex(ValueError,'REGISTRY_SHA256'):self.run_build(True)

    def test_unrequested_overwrite_and_source_change_rejected(self):
        self.run_build()
        with self.assertRaisesRegex(ValueError,'EXPLICIT_RESUME'):self.run_build()
        self.fx.case.p.write_text(self.fx.case.p.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'SOURCE_SHA256'):self.run_build(True)

    def test_drifted_official_metadata_rejected_before_ocr_or_resume(self):
        sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        image=self.root/'image.jpg';image.write_bytes(b'synthetic original image')
        metadata=self.root/'official.json';metadata.write_text('{"license_code":"CC BY"}')
        receipt=self.root/'receipt.json';receipt.write_text(json.dumps({'official_metadata_path':str(metadata),'official_metadata_sha256':sha(metadata)}))
        self.data['cases'][0]['figures']=[{'figure_id':'F','image_path':str(image),'image_sha256':sha(image),'acquisition_receipt':str(receipt),'receipt_sha256':sha(receipt)}]
        self.manifest.write_text(json.dumps(self.data));self.hash=sha(self.manifest)
        metadata.write_text('{"license_code":"CHANGED"}')
        with self.assertRaisesRegex(ValueError,'OFFICIAL_METADATA_SHA256'):self.run_build()
        self.assertFalse((self.root/'build').exists())

    def test_end_to_end_build_replays_parent_linked_supplement_context(self):
        self.add_verified_supplement()
        result=self.run_build()
        packet=json.loads((self.root/'build/relations/PMC000.json').read_text())
        self.assertTrue(packet['supplementary_link_audit']['unique_paper_scope_link_proposed'])
        self.assertTrue(packet['candidates'][0]['supplementary_method_context'])
        self.assertEqual(packet['supplementary_documents'][0]['acquisition_context']['parent_source_sha256'],
                         packet['source_sha256'])
        self.assertEqual(result['counts']['Train-A'],0)
        db=self.root/'build/three_table_candidates_v001/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3'
        with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as conn:
            record=json.loads(conn.execute('select source_locator from observations').fetchone()[0])
            self.assertEqual(record['supplementary_method_context'][0]['source_sha256'],
                             packet['supplementary_documents'][0]['source_sha256'])
            self.assertTrue(record['supplementary_method_context'][0]['author_literal'].startswith(
                            'Intracellular ATP levels were measured'))
            self.assertFalse(record['supplementary_method_review']['training_approved'])
        export_manifest=json.loads((self.root/'build/three_table_candidates_v001/MANIFEST.json').read_text())
        self.assertEqual(len(export_manifest['supplementary_assets']),1)

    def test_build_fails_closed_for_permission_or_supplement_byte_drift(self):
        self.add_verified_supplement(permission_status='LICENSE_HOLD')
        with self.assertRaisesRegex(ValueError,'PERMISSION_NOT_VERIFIED'):
            self.run_build()
        self.assertFalse((self.root/'build').exists())

        self.add_verified_supplement()
        path=Path(self.data['cases'][0]['supplementary_sources'][0]['source_path'])
        path.write_bytes(path.read_bytes()+b'drift')
        with self.assertRaisesRegex(ValueError,'SOURCE_SHA256_MISMATCH'):
            self.run_build()
        self.assertFalse((self.root/'build').exists())
