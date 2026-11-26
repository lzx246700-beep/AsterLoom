import unittest
import tempfile
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from r031_evidence.atp_content_figure import summarize_tokens
from r031_evidence.atp_content_figure import extract_figure


class Figure(unittest.TestCase):
    def test_dose_row_has_coordinate_evidence_and_no_atp_values(self):
        tokens = [{'text':'TYP (uM)','bbox':[.39,.05,.05,.016],'ocr_confidence':.8},
                  {'text':'40','bbox':[.55,.05,.01,.016],'ocr_confidence':.7},
                  {'text':'80','bbox':[.58,.05,.01,.016],'ocr_confidence':.7},
                  {'text':'100','bbox':[.61,.05,.02,.016],'ocr_confidence':.7},
                  {'text':'ATP level (fold of LPS plus PA)','bbox':[.40,.17,.01,.20],'ocr_confidence':.6},
                  {'text':'5 μm','bbox':[.55,.15,.02,.01],'ocr_confidence':.8}]
        result=summarize_tokens(tokens)
        row,=result['dose_row_candidates']
        self.assertEqual([t['text'] for t in row['dose_token_candidates']],['40','80','100'])
        self.assertEqual(len(result['axis_text_candidates']),1)
        self.assertEqual(result['data_origin'],'OCR_TEXT_ONLY_NO_AUTHOR_NUMERIC_VALUE')
        self.assertIsNone(result['treated_value']);self.assertIsNone(result['control_value'])

    def test_ocr_picomolar_error_is_not_silently_corrected(self):
        r=summarize_tokens([{'text':'TYP (100 pM)','bbox':[0,0,.1,.1],'ocr_confidence':.99}])
        self.assertEqual(r['dose_row_candidates'],[])
        self.assertIn('pM',r['relevant_tokens'][0]['text'])

    def test_rotated_arm_labels_remain_unassigned_text_candidates(self):
        view={'angle_degrees':-45,'coordinate_basis':'rotated normalized coordinates',
              'rgba_pixel_sha256':'a'*64,
              'tokens':[{'text':'NAM-PNT60','bbox':[.4,.7,.1,.02],'ocr_confidence':.5}]}
        result=summarize_tokens([], [view])
        row,=result['rotated_view_text_candidates']
        self.assertEqual(row['text'],'NAM-PNT60')
        self.assertEqual(row['state'],'ROTATED_VIEW_TEXT_CANDIDATE_PANEL_AND_ARM_UNASSIGNED')
        self.assertEqual(row['view_pixel_sha256'],'a'*64)
        self.assertEqual(result['relevant_tokens'],[])
        self.assertIsNone(result['treated_value']);self.assertIsNone(result['control_value'])

    def test_publisher_filename_without_article_identity_cannot_bind(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);s=p/'a.xml';im=p/'a.jpg';receipt=p/'r.json'
            im.write_bytes(b'SYNTHETIC_TEST_NOT_SCIENCE');digest=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
            for ids,url in [('', 'https://media.springernature.com/full/other/asset.jpg'),
                            ('<article-id pub-id-type="doi">10.1/correct</article-id>', 'https://media.springernature.com/full/10.1/wrong/asset.jpg'),
                            ('<article-id pub-id-type="pii">S12345</article-id>', 'https://ars.els-cdn.com/content/image/1-s2.0-S99999-asset.jpg')]:
                s.write_text('<article xmlns:xlink="http://www.w3.org/1999/xlink"><front>'+ids+'</front><body><fig id="F"><graphic xlink:href="asset.jpg"/></fig></body></article>')
                receipt.write_text(json.dumps({'figure_id':'F','sha256':digest(im),'final_url':url}))
                with self.subTest(url=url),patch('r031_evidence.atp_content_figure.subprocess.run') as run:
                    with self.assertRaisesRegex(ValueError,'DECLARED_ASSET_MISMATCH'):
                        extract_figure(s,digest(s),'F',im,digest(im),receipt)
                    run.assert_not_called()

    def test_official_pmc_media_requires_exact_identity_license_and_md5(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);source=p/'a.xml';im=p/'a.jpg';meta=p/'meta.json';receipt=p/'r.json'
            source.write_text('<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-id pub-id-type="pmcid">PMC1234</article-id><article-id pub-id-type="doi">10.1/test</article-id></front><body><fig id="F"><caption>ATP</caption><graphic xlink:href="asset.jpg"/></fig></body></article>')
            im.write_bytes(b'SYNTHETIC_NO_AUTHOR_DATA');sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
            md5=hashlib.md5(im.read_bytes()).hexdigest();url='https://pmc-oa-opendata.s3.amazonaws.com/PMC1234.1/asset.jpg?md5='+md5
            data={'pmcid':'PMC1234','doi':'10.1/test','version':1,'license_code':'CC BY','is_retracted':False,
                  'media_urls':['s3://pmc-oa-opendata/PMC1234.1/asset.jpg?md5='+md5]}
            def write(d):
                meta.write_text(json.dumps(d));receipt.write_text(json.dumps({'sha256':sha(im),'figure_id':'F','final_url':url,
                    'official_metadata_path':str(meta),'official_metadata_sha256':sha(meta),'article_version':1}))
            for changed in [{'doi':'10.1/other'},{'license_code':'CC BY-NC-ND'},{'is_retracted':True},
                            {'media_urls':['s3://pmc-oa-opendata/PMC1234.1/other.jpg?md5='+md5]}]:
                write({**data,**changed})
                with self.subTest(changed=changed),patch('r031_evidence.atp_content_figure.subprocess.run') as run:
                    with self.assertRaises(ValueError):extract_figure(source,sha(source),'F',im,sha(im),receipt)
                    run.assert_not_called()
            write(data);im.write_bytes(b'CHANGED_BYTES')
            r=json.loads(receipt.read_text());r['sha256']=sha(im);receipt.write_text(json.dumps(r))
            with self.assertRaisesRegex(ValueError,'ASSET_MISMATCH'):extract_figure(source,sha(source),'F',im,sha(im),receipt)

    def test_ncbi_article_cdn_requires_pmcid_jats_license_and_full_etag_md5(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);source=p/'a.xml';im=p/'a.jpg';receipt=p/'r.json'
            lic='https://creativecommons.org/licenses/by/4.0/'
            source.write_text('<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-id pub-id-type="pmcid">PMC1234</article-id><article-id pub-id-type="doi">10.1/test</article-id></front><body><fig id="F"><caption>ATP</caption><graphic xlink:href="asset.jpg"/></fig></body><permissions><license href="'+lic+'">CC BY 4.0</license></permissions></article>')
            im.write_bytes(b'SYNTHETIC_NCBI_CDN_ASSET');sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
            md5=hashlib.md5(im.read_bytes()).hexdigest();url=f'https://cdn.ncbi.nlm.nih.gov/pmc/blobs/abcd/1234/{md5[:12]}/asset.jpg'
            lic_literal=[' '.join(''.join(e.itertext()).split()) for e in __import__('xml.etree.ElementTree',fromlist=['']).parse(source).getroot().iter() if e.tag.split('}')[-1]=='license']
            base={'sha256':sha(im),'figure':'F','pmcid':'PMC1234','doi':'10.1/test','source_sha256':sha(source),
                  'jats_graphic_href':'asset.jpg','license_author_literal':lic_literal,'request_url':url,'final_url':url,
                  'http_status':200,'content_type':'image/jpeg','etag':'"'+md5+'"'}
            def write(d):receipt.write_text(json.dumps(d))
            with patch('r031_evidence.atp_content_figure.subprocess.run',return_value=SimpleNamespace(stdout='{"width":10,"height":10,"tokens":[]}')) as run:
                write(base);packet=extract_figure(source,sha(source),'F',im,sha(im),receipt)
                self.assertTrue(packet['official_cdn_evidence']['etag_md5_verified'])
                self.assertEqual(packet['official_cdn_evidence']['pmcid'],'PMC1234')
                self.assertEqual(packet['license_author_literal'],lic_literal)
                for changed in [{'pmcid':'PMC9999'},{'doi':'10.1/other'},{'etag':'"'+'0'*32+'"'},
                                {'request_url':'https://example.org/'+url.rsplit('/',1)[1]},
                                {'jats_graphic_href':'other.jpg'}]:
                    write({**base,**changed})
                    with self.subTest(changed=changed):
                        with self.assertRaisesRegex(ValueError,'DECLARED_ASSET_MISMATCH'):
                            extract_figure(source,sha(source),'F',im,sha(im),receipt)
            source.write_text(source.read_text().replace('CC BY 4.0','CC BY-NC-ND 4.0'))
            restricted=[' '.join(''.join(e.itertext()).split()) for e in __import__('xml.etree.ElementTree',fromlist=['']).parse(source).getroot().iter() if e.tag.split('}')[-1]=='license']
            write({**base,'source_sha256':sha(source),'license_author_literal':restricted})
            with patch('r031_evidence.atp_content_figure.subprocess.run') as run:
                with self.assertRaisesRegex(ValueError,'DECLARED_ASSET_MISMATCH'):
                    extract_figure(source,sha(source),'F',im,sha(im),receipt)
                run.assert_not_called()

    def test_mdpi_author_asset_requires_doi_jats_license_and_link_receipt(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);source=p/'a.xml';im=p/'asset.png';receipt=p/'r.json'
            license_text=('Licensee MDPI, Basel, Switzerland. This article is an open access article '
                'distributed under the terms and conditions of the Creative Commons Attribution (CC BY) license.')
            source.write_text('<article xmlns:xlink="http://www.w3.org/1999/xlink"><front>'
                '<article-id pub-id-type="doi">10.1/test</article-id></front><body>'
                '<fig id="F"><caption>ATP</caption><graphic xlink:href="asset.webp"/></fig></body>'
                '<permissions><license>'+license_text+'</license></permissions></article>')
            im.write_bytes(b'SYNTHETIC_MDPi_FIGURE_BYTES');sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
            url='https://mdpi-res.com/journal/article/asset.png';article_url='https://www.mdpi.com/2076-3921/15/9/1079'
            base={'sha256':sha(im),'figure':'F','source_sha256':sha(source),'doi':'10.1/test',
                  'jats_graphic_href':'asset.webp','publisher_article_url':article_url,
                  'publisher_asset_link_verified':True,'license_author_literal':[license_text],
                  'request_url':url,'final_url':url,'http_status':200,'content_type':'image/png'}
            def write(d):receipt.write_text(json.dumps(d))
            with patch('r031_evidence.atp_content_figure.subprocess.run',
                       return_value=SimpleNamespace(stdout='{"width":10,"height":10,"tokens":[]}')):
                write(base);packet=extract_figure(source,sha(source),'F',im,sha(im),receipt)
                evidence=packet['publisher_asset_evidence']
                self.assertEqual(evidence['doi'],'10.1/test')
                self.assertEqual(evidence['license_basis'],'AUTHOR_JATS_CC_BY_UNVERSIONED_TEXT_AND_PUBLISHER_LINK_RECEIPT')
                self.assertFalse(evidence['license_version_resolved'])
                self.assertIsNone(packet['index']['treated_value'])
                for changed in [{'doi':'10.1/other'},
                                {'publisher_asset_link_verified':False},
                                {'jats_graphic_href':'other.webp'},
                                {'license_author_literal':['CC BY-NC-ND']},
                                {'source_sha256':'0'*64}]:
                    write({**base,**changed})
                    with self.subTest(changed=changed),patch('r031_evidence.atp_content_figure.subprocess.run') as run:
                        with self.assertRaisesRegex(ValueError,'DECLARED_ASSET_MISMATCH'):
                            extract_figure(source,sha(source),'F',im,sha(im),receipt)
                        run.assert_not_called()

    def test_unversioned_author_license_requires_explicit_unrestricted_attribution_text(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);source=p/'a.xml';im=p/'a.jpg';receipt=p/'r.json'
            license_text=('This is an open-access article distributed under the terms of the '
                'Creative Commons Attribution License, which permits unrestricted use, distribution, '
                'and reproduction in any medium, provided the original work is properly cited.')
            source.write_text('<article xmlns:xlink="http://www.w3.org/1999/xlink"><front>'
                '<article-id pub-id-type="pmcid">PMC1234</article-id>'
                '<article-id pub-id-type="doi">10.1/test</article-id></front><body>'
                '<fig id="F"><caption>ATP</caption><graphic xlink:href="asset.jpg"/></fig></body>'
                '<permissions><license>'+license_text+'</license></permissions></article>')
            im.write_bytes(b'SYNTHETIC_NCBI_CDN_ASSET');sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
            md5=hashlib.md5(im.read_bytes()).hexdigest();url=f'https://cdn.ncbi.nlm.nih.gov/pmc/blobs/abcd/1234/{md5[:12]}/asset.jpg'
            license_literal=[' '.join(license_text.split())]
            receipt.write_text(json.dumps({'sha256':sha(im),'figure':'F','pmcid':'PMC1234','doi':'10.1/test',
                'source_sha256':sha(source),'jats_graphic_href':'asset.jpg','license_author_literal':license_literal,
                'request_url':url,'final_url':url,'http_status':200,'content_type':'image/jpeg','etag':'"'+md5+'"'}))
            with patch('r031_evidence.atp_content_figure.subprocess.run',
                       return_value=SimpleNamespace(stdout='{"width":10,"height":10,"tokens":[]}')):
                packet=extract_figure(source,sha(source),'F',im,sha(im),receipt)
            evidence=packet['official_cdn_evidence']
            self.assertEqual(evidence['license_basis'],'AUTHOR_JATS_EXPLICIT_UNRESTRICTED_ATTRIBUTION_VERSION_UNSPECIFIED')
            self.assertFalse(evidence['license_version_resolved'])
            restricted_cases=[license_text.replace('unrestricted use, distribution, and reproduction in any medium,',
                    'non-commercial use and distribution only,'),
                license_text.replace('Creative Commons Attribution License','Creative Commons Attribution-ShareAlike License'),
                license_text.replace('Creative Commons Attribution License','Creative Commons Attribution-NoDerivatives License')]
            for restricted in restricted_cases:
                source.write_text(source.read_text().replace(license_text,restricted))
                receipt_data=json.loads(receipt.read_text());receipt_data['source_sha256']=sha(source)
                receipt_data['license_author_literal']=[' '.join(restricted.split())];receipt.write_text(json.dumps(receipt_data))
                with patch('r031_evidence.atp_content_figure.subprocess.run') as run:
                    with self.assertRaisesRegex(ValueError,'DECLARED_ASSET_MISMATCH'):
                        extract_figure(source,sha(source),'F',im,sha(im),receipt)
                    run.assert_not_called()
                source.write_text(source.read_text().replace(restricted,license_text))
