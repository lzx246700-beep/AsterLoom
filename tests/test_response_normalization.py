from r031_evidence.response_normalization import normalize,receipt

def test_partial_metadata_and_null_extra_are_audited_without_changing_original():
 original={'paper':'PMC1','relations':[{'evidence':[{'field_label':None,'field':'dose'}]}]}
 out,changes=normalize(original,{'coverage_complete':False})
 assert out['complete'] is False and out['reason_if_none'] is None
 assert 'field_label' not in out['relations'][0]['evidence'][0]
 assert original['relations'][0]['evidence'][0]['field_label'] is None
 assert len(changes)==4 and receipt(original,out,changes,'a'*64)['raw_response_sha256']=='a'*64

def test_nonempty_unknown_and_missing_coverage_are_not_repaired():
 original={'relations':[{'evidence':[{'field_label':'meaningful'}]}]}
 out,changes=normalize(original,None)
 assert out==original and changes==[]

def test_extra_value_only_removed_if_identical_to_field_and_quote():
 original={'relations':[{'fields':{'value':'135%'},'evidence':[{'field':'direction','quote':'approximately 135%','value':'135%'}]}]}
 out,changes=normalize(original,None)
 assert 'value' not in out['relations'][0]['evidence'][0] and changes[0]['value']=='135%'
 original['relations'][0]['evidence'][0]['value']='different'
 assert normalize(original,None)[1]==[]
