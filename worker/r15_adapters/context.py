"""Generic, anchored context candidates. Unit algebra does not establish assay type."""
import re
import unicodedata

def context_candidates(table):
    headers=table.headers
    notes=table.footnotes
    out={'units':[],'statistics':[],'conditions':[],'state':'FIELD_LEVEL_REVIEW_HOLD'}
    for h in headers:
        raw=h['raw_text']
        # Deliberately narrow structural recognizer; never fill missing author text.
        if re.search(r'[µμu]mol',raw) and re.search(r'g\s+liver\s+weight',raw,re.I):
            normalized=unicodedata.normalize('NFKC',raw).replace('μ','u').replace('µ','u').replace('−','-')
            if re.search(r'\(g liver weight\)\s*-1',normalized):
                out['units'].append({'raw_text':raw,'semantic_unit':'micromole / gram','tissue_basis_literal':'liver weight','anchor':h['anchor'],
                    'column':h.get('column'),'colspan':h.get('colspan'),'state':'UNIT_CANDIDATE_REVIEW_HOLD'})
    for n in notes:
        if re.search(r'standard errors?(?: of the mean)?',n['raw_text'],re.I):
            out['statistics'].append({'raw_text':n['raw_text'],'statistic_candidate':'SEM','anchor':n['anchor']})
    # Table captions remain separate from Methods; conditions are never pooled.
    for label,pat in [('fasted',r'\bfasted\b'),('fed',r'\bfed\b')]:
        if re.search(pat,table.caption,re.I):out['conditions'].append({'condition_literal':label,'caption_raw':table.caption,'table_anchor':table.anchor.__dict__,'state':'CAPTION_CONTEXT_METHODS_LINKAGE_HOLD'})
    return out
