"""Optional local figure-text evidence; no bar-height numerical extraction.

Does not change JATS/table parsing. Apple OCR has no model API or new package.
All tokens retain original image SHA and coordinates; no OCR confidence is
reported as scientific accuracy, and no observation is approved from OCR.
"""
from __future__ import annotations
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

from .source_io import local, plain


def summarize_tokens(tokens, rotated_views=()):
    """Conservative candidate index; multiple doses require matrix review.

    Tokens on different panels are never merged into an experiment. The image
    is indexed only; recognizing a TYP row is not assigning that row to a bar.
    """
    relevant = []
    for i, t in enumerate(tokens):
        if re.search(r'\bATP\b|\bLPS\b|\bPA\b|\bTYP\b|\bPTF\b|\bAICAR\b|PF-05175157', t['text'], re.I):
            relevant.append({'token_id': i, **t, 'state': 'IMAGE_TEXT_CANDIDATE_NOT_ARM_ASSIGNMENT'})
    rows = []
    for i, label in enumerate(tokens):
        m = re.fullmatch(r'\s*([A-Za-z][\w-]*)\s*\(([uµμ]M|mM|nM)\)\s*', label['text'])
        if not m: continue
        x,y,w,h = label['bbox']; mid = y+h/2
        values = []
        for j, token in enumerate(tokens):
            a,b,c,d = token['bbox']
            if a > x+w and a < min(1, x+w+.35) and abs(b+d/2-mid) < max(h,d)*.5 and re.fullmatch(r'\d+(?:\.\d+)?',token['text']):
                values.append({'token_id':j, **token})
        if values:
            rows.append({'agent_ocr_literal':m[1], 'unit_ocr_literal':m[2],
                'label_token_id':i, 'dose_token_candidates':sorted(values,key=lambda v:v['bbox'][0]),
                'state':'IMAGE_DOSE_ROW_CANDIDATE_NOT_EXPERIMENT_OR_ATP_VALUE'})
    axes = [{'token_id':i, **t, 'state':'OCR_AXIS_TEXT_UNREVIEWED'} for i,t in enumerate(tokens)
            if re.search(r'ATP\s+(?:level|content).{0,20}fold',t['text'],re.I)]
    rotated_candidates=[]
    for view_index,view in enumerate(rotated_views):
        for token_index,token in enumerate(view.get('tokens',[])):
            if re.search(r'\b(?:ATP|NAM|NAD|PNT|LPS|PA|TYP)\b|ATP|PNT|NAM-',token.get('text',''),re.I):
                rotated_candidates.append({
                    'view_index':view_index,'token_id':token_index,**token,
                    'angle_degrees':view.get('angle_degrees'),
                    'coordinate_basis':view.get('coordinate_basis'),
                    'view_pixel_sha256':view.get('rgba_pixel_sha256'),
                    'state':'ROTATED_VIEW_TEXT_CANDIDATE_PANEL_AND_ARM_UNASSIGNED'})
    return {'relevant_tokens': relevant, 'dose_row_candidates':rows, 'axis_text_candidates':axes,
            'rotated_view_text_candidates':rotated_candidates,
            'axis_unit': None, 'treated_value': None, 'control_value': None,
            'data_origin': 'OCR_TEXT_ONLY_NO_AUTHOR_NUMERIC_VALUE',
            'hold_reasons': ['OCR_TOKEN_ACCURACY_UNREVIEWED', 'IMAGE_CONDITION_MATRIX_UNBOUND',
                             'BAR_HEIGHTS_NOT_AUTHOR_REPORTED_NUMERIC_VALUES']}


def extract_figure(source_path, source_sha, figure_id, image_path, image_sha, acquisition_receipt,
                   timeout=45):
    source_path = Path(source_path).resolve(); image_path = Path(image_path).resolve()
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    if sha(source_path) != source_sha or sha(image_path) != image_sha:
        raise ValueError('ATP_FIGURE_SOURCE_SHA256_MISMATCH')
    raw_receipt = Path(acquisition_receipt).read_bytes(); receipt = json.loads(raw_receipt)
    if receipt.get('sha256') != image_sha or receipt.get('figure',receipt.get('figure_id')) != figure_id:
        raise ValueError('ATP_IMAGE_ACQUISITION_BINDING_MISMATCH')
    root = ET.fromstring(source_path.read_bytes())
    figs = [e for e in root.iter() if local(e.tag) == 'fig' and e.get('id') == figure_id]
    if len(figs) != 1: raise ValueError('ATP_FIGURE_ID_UNRESOLVED')
    hrefs = [e.get('{http://www.w3.org/1999/xlink}href') for e in figs[0].iter() if local(e.tag) == 'graphic']
    fetched = urlparse(receipt.get('final_url', ''))
    if fetched.scheme != 'https' or fetched.hostname not in ('media.springernature.com','ars.els-cdn.com','pmc-oa-opendata.s3.amazonaws.com','cdn.ncbi.nlm.nih.gov','mdpi-res.com'):
        raise ValueError('ATP_IMAGE_PUBLISHER_RECEIPT_UNSUPPORTED')
    ids={e.get('pub-id-type'):plain(e) for e in root.iter() if local(e.tag)=='article-id'}
    filename=Path(unquote(fetched.path)).name
    if fetched.hostname=='pmc-oa-opendata.s3.amazonaws.com':
        from .r17_official_si import _url_for_media
        meta_path=Path(receipt.get('official_metadata_path',''))
        if not meta_path.is_file() or sha(meta_path)!=receipt.get('official_metadata_sha256'):
            raise ValueError('ATP_IMAGE_OFFICIAL_METADATA_SHA_HOLD')
        meta=json.loads(meta_path.read_text());pmcid=ids.get('pmcid');version=meta.get('version')
        if (not pmcid or meta.get('pmcid')!=pmcid or meta.get('doi')!=ids.get('doi') or version!=receipt.get('article_version')
            or meta.get('license_code') not in ('CC BY','CC0','CC BY-SA') or meta.get('is_retracted') is not False):
            raise ValueError('ATP_IMAGE_OFFICIAL_METADATA_IDENTITY_OR_LICENSE_HOLD')
        links=[_url_for_media(u,pmcid,version,filename) for u in meta.get('media_urls',[])];links=[u for u in links if u]
        matched=(filename in hrefs and len(links)==1 and links[0][0]==receipt['final_url']
                 and hashlib.md5(image_path.read_bytes()).hexdigest()==links[0][1])
    elif fetched.hostname=='cdn.ncbi.nlm.nih.gov':
        # NCBI's article CDN exposes versioned PMC blobs with the PMCID,
        # JATS filename, a content-MD5 prefix in the path, and the full MD5
        # as the HTTP ETag. Accept only a frozen acquisition receipt that
        # records the matching 200 response, source JATS identity and license.
        pmcid=ids.get('pmcid','');normalized=pmcid.removeprefix('PMC')
        parts=[unquote(x) for x in fetched.path.split('/') if x]
        cdn_match=re.fullmatch(r'pmc/blobs/[0-9a-f]{4}/([0-9]+)/([0-9a-f]{12})/([^/]+)',
                               '/'.join(parts),re.I)
        etag=receipt.get('etag','').strip().strip('"').lower()
        md5=hashlib.md5(image_path.read_bytes()).hexdigest()
        license_elements=[e for e in root.iter() if local(e.tag)=='license']
        license_literals=[plain(e) for e in license_elements]
        license_scopes=[(plain(e)+' '+' '.join(str(v) for k,v in e.attrib.items()
            if k.rsplit('}',1)[-1].lower() in ('href','license-type'))).lower() for e in license_elements]
        cc_by_4=any('creativecommons.org/licenses/by/4.0' in x or 'cc by 4.0' in x for x in license_scopes)
        # Some older JATS records omit a versioned CC URI while the article's
        # own license text explicitly grants unrestricted reuse with attribution.
        # Accept that narrower evidence class without inventing a version.
        explicit_unrestricted_attribution=any(
            'creative commons attribution license' in x and
            'unrestricted use' in x and 'distribution' in x and
            ('properly cited' in x or 'properly credited' in x or
             'source are credited' in x or 'source is credited' in x)
            for x in license_scopes)
        license_version_resolved=cc_by_4
        license_restricted=any(term in x for x in license_scopes for term in
            ('by-nc','by-nd','by-sa','noncommercial','non-commercial','share-alike',
             'share alike','no derivatives','non-derivative'))
        matched=bool(cdn_match and normalized and cdn_match[1]==normalized and
            filename in hrefs and cdn_match[3]==filename and cdn_match[2]==md5[:12] and
            etag==md5 and receipt.get('http_status')==200 and
            receipt.get('content_type','').lower().startswith('image/') and
            receipt.get('pmcid')==pmcid and receipt.get('doi')==ids.get('doi') and
            receipt.get('source_sha256')==source_sha and
            receipt.get('jats_graphic_href')==filename and (cc_by_4 or explicit_unrestricted_attribution) and
            receipt.get('license_author_literal')==license_literals and not license_restricted and
            receipt.get('request_url')==receipt.get('final_url')==unquote(receipt.get('final_url','')) and
            fetched.query=='')
    elif fetched.hostname=='mdpi-res.com':
        # MDPI author JATS currently exposes a version-unspecified CC BY
        # statement without a machine-readable license href. Require the exact
        # article identity, JATS-declared filename stem, unchanged author
        # source, and a receipt tying the asset link to the publisher article.
        license_elements=[e for e in root.iter() if local(e.tag)=='license']
        license_literals=[plain(e) for e in license_elements]
        license_text=' '.join(license_literals).casefold()
        cc_by_literal='creative commons attribution (cc by) license' in license_text
        license_restricted=any(term in license_text for term in
            ('by-nc','by-nd','by-sa','noncommercial','non-commercial','share-alike',
             'share alike','no derivatives','non-derivative'))
        publisher_article=urlparse(receipt.get('publisher_article_url',''))
        declared_names={Path(unquote(h)).stem for h in hrefs if h}
        matched=bool(ids.get('doi') and filename and Path(filename).stem in declared_names and
            receipt.get('sha256')==image_sha and receipt.get('figure',receipt.get('figure_id'))==figure_id and
            receipt.get('source_sha256')==source_sha and receipt.get('doi')==ids.get('doi') and
            receipt.get('jats_graphic_href') in hrefs and
            receipt.get('publisher_asset_link_verified') is True and
            publisher_article.scheme=='https' and publisher_article.hostname=='www.mdpi.com' and
            cc_by_literal and not license_restricted and receipt.get('license_author_literal')==license_literals and
            receipt.get('http_status')==200 and receipt.get('content_type','').lower().startswith('image/') and
            receipt.get('request_url')==receipt.get('final_url')==unquote(receipt.get('final_url','')) and
            fetched.query=='')
    elif fetched.hostname=='ars.els-cdn.com':
        pii=re.sub(r'[^A-Za-z0-9]','',ids.get('pii',''))
        allowed={'1-s2.0-'+pii+'-'+Path(h).name for h in hrefs if h} if pii else set()
        matched=filename in allowed
    else:
        matched=bool(ids.get('doi')) and Path(filename).stem in {Path(h).stem for h in hrefs if h} and ids['doi'] in unquote(fetched.path)
    if not matched:
        raise ValueError('ATP_IMAGE_DECLARED_ASSET_MISMATCH')
    # Pixel-text extraction is local and bounded. Missing backend safely HOLDs.
    swift = shutil.which('swift')
    if not swift: raise ValueError('ATP_OPTIONAL_MACOS_VISION_BACKEND_UNAVAILABLE')
    script = Path(__file__).parent/'data/atp_figure_ocr.swift'
    result = subprocess.run([swift, str(script), str(image_path)], check=True, capture_output=True,
                            timeout=min(60,max(1,timeout)), text=True)
    data = json.loads(result.stdout)
    if sha(source_path) != source_sha or sha(image_path) != image_sha:
        raise ValueError('ATP_FIGURE_SOURCE_CHANGED_DURING_OCR')
    return {'schema':'asterloom.atp-content.figure-text/0.1', 'source_path':str(source_path),
            'source_sha256':source_sha, 'figure_id':figure_id,
            'image_path':str(image_path), 'image_sha256':image_sha,
            'acquisition_receipt_path':str(Path(acquisition_receipt).resolve()),
            'acquisition_receipt_sha256':hashlib.sha256(raw_receipt).hexdigest(),
            'official_metadata_evidence':({'path':receipt['official_metadata_path'],'sha256':receipt['official_metadata_sha256'],
                'license_code':meta['license_code'],'media_md5_verified':True} if fetched.hostname=='pmc-oa-opendata.s3.amazonaws.com' else None),
            'official_cdn_evidence':({'host':fetched.hostname,'pmcid':ids.get('pmcid'),'doi':ids.get('doi'),
                'etag_md5_verified':True,'author_license_literal':[plain(e) for e in root.iter() if local(e.tag)=='license'],
                'license_version_resolved':license_version_resolved,
                'license_basis':('CC_BY_4.0_JATS_URI' if cc_by_4 else
                    'AUTHOR_JATS_EXPLICIT_UNRESTRICTED_ATTRIBUTION_VERSION_UNSPECIFIED') if (cc_by_4 or explicit_unrestricted_attribution) else 'HOLD',
                'acquisition_receipt_sha256':hashlib.sha256(raw_receipt).hexdigest()} if fetched.hostname=='cdn.ncbi.nlm.nih.gov' else None),
            'publisher_asset_evidence':({'host':fetched.hostname,'doi':ids.get('doi'),
                'publisher_article_url':receipt['publisher_article_url'],
                'jats_graphic_href':receipt['jats_graphic_href'],
                'author_license_literal':license_literals,'license_version_resolved':False,
                'license_basis':'AUTHOR_JATS_CC_BY_UNVERSIONED_TEXT_AND_PUBLISHER_LINK_RECEIPT',
                'acquisition_receipt_sha256':hashlib.sha256(raw_receipt).hexdigest()}
                if fetched.hostname=='mdpi-res.com' else None),
            'figure_caption_author_literal':plain(next((e for e in figs[0] if local(e.tag)=='caption'),None)),
            'license_author_literal':[plain(e) for e in root.iter() if local(e.tag)=='license'],
            'ocr':data, 'index':summarize_tokens(data['tokens'],data.get('rotated_views',[])),
            'network_requests':0, 'paid_model_calls':0, 'training_approved':False,
            'status':'FIGURE_TEXT_EVIDENCE_FOR_REVIEW_NO_NUMERIC_LABELS'}
