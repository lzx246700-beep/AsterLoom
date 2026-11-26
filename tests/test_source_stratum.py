from unittest.mock import patch
from r031_evidence.source_stratum import verify_source_stratum

def test_stratum_requires_unique_author_bound_arms_and_values(tmp_path):
    source=tmp_path/'author.xml';source.write_text('fixture')
    packet={'source_path':str(source),'source_sha256':'a'*64,'paper':'PMC1'}
    original={'scope':{'author_stratum':'005x'}}
    pair={'basis':'SAME_SENTENCE_ORDERED_STRATA','tier_hint':'A_CANDIDATE','stratum':'005x',
      'treated':{'stratum':'005x','chunk_id':'c','arm_label':'the Res group','value':8.51,'unit':'pmol'},
      'control':{'stratum':'005x','chunk_id':'c','arm_label':'controls','value':5.10,'unit':'pmol'}}
    measured=[{'arm':'TREATED','chunk_id':'c','arm_label':'Res group','value':8.51,'unit_literal':'pmol'},
              {'arm':'CONTROL','chunk_id':'c','arm_label':'controls','value':5.10,'unit_literal':'pmol'}]
    with patch('r031_evidence.source_stratum._pairs',return_value=[pair]):
        assert verify_source_stratum(packet,original,measured)=='005x'
        assert verify_source_stratum(packet,{'scope':{'author_stratum':'011x'}},measured) is False
        measured[1]['value']=6.85
        assert verify_source_stratum(packet,original,measured) is False
    assert verify_source_stratum(packet,{'scope':{}},measured) is None
