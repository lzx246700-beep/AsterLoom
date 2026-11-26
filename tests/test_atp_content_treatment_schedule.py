from r031_evidence.atp_content_treatment_schedule import extract_arm_schedule_candidates


def evidence(text):
    return {'source_sha256': 'a' * 64, 'source_anchor': '/article/sec[2]/p[4]',
            'quote': text, 'start': 100, 'end': 100 + len(text),
            'offset_basis': 'source_io.plain normalized Unicode characters'}


def test_separates_pretreatment_and_intervention_dose_time_without_writing_fields():
    text = ('The MT + MBP group underwent pre-treatment with 5 μM MT for 6 h, '
            'followed by 24 h of treatment with 13 μM MBP.')
    result = extract_arm_schedule_candidates(
        text, evidence(text), 'MT + MBP', ['control', 'MBP', 'MT + MBP'])
    assert [(x['author_agent_literal'], x['dose_value_literal'], x['dose_unit_literal'],
             x['phase'], x['duration_value_literal'], x['duration_unit_literal']) for x in result] == [
                 ('MT', '5', 'μM', 'PRETREATMENT', '6', 'h'),
                 ('MBP', '13', 'μM', 'TREATMENT', '24', 'h')]
    for item in result:
        assert item['dose_evidence']['source_sha256'] == 'a' * 64
        assert text[item['dose_evidence']['start'] - 100:item['dose_evidence']['end'] - 100] == item['dose_original_literal']
        assert item['normalized_candidate_fields_written'] is False
        assert item['panel_binding_inferred'] is False


def test_single_treatment_clause_does_not_inherit_prior_culture_duration():
    text = 'Cells in the MBP group were cultured for 6 h and then treated with 13 μM MBP for 24 h.'
    result = extract_arm_schedule_candidates(text, evidence(text), 'MBP', ['control', 'MBP'])
    assert len(result) == 1
    assert result[0]['duration_value_literal'] == '24'
    assert result[0]['phase'] == 'TREATMENT'


def test_multi_arm_sentence_is_held_instead_of_copying_conditions():
    text = ('During the MT pre-treatment period (6 h) and the drug intervention period (24 h), '
            'cells in the control group were cultured in medium, while those in the DMSO group '
            'received 0.04% DMSO for 24 h.')
    result = extract_arm_schedule_candidates(text, evidence(text), 'DMSO', ['control', 'DMSO'])
    assert len(result) == 1
    assert result[0]['state'] == 'MULTIPLE_ARM_SENTENCE_CONDITIONS_NOT_SPLIT'
    assert 'dose_value_literal' not in result[0]


def test_dose_is_only_assigned_to_named_arm_component_and_mass_unit_is_not_converted():
    text = 'Cells in the compound group received 2 mg/mL compound for 48 h.'
    result = extract_arm_schedule_candidates(text, evidence(text), 'compound', ['compound'])
    assert result[0]['dose_value_literal'] == '2'
    assert result[0]['dose_unit_literal'] == 'mg/mL'
    assert result[0]['duration_value_literal'] == '48'


def test_after_treatment_followed_by_treated_clause_keeps_first_phase_role_held():
    text = ('In the MBP + MT + EX-527 group, after treatment with 5 μM MT for 6 h, '
            'cells were treated for 24 h with 13 μM MBP and 10 μM EX-527.')
    result = extract_arm_schedule_candidates(
        text, evidence(text), 'MT + MBP + EX-527', ['MT + MBP + EX-527'])
    mt = next(x for x in result if x['author_agent_literal'] == 'MT')
    assert mt['dose_value_literal'] == '5'
    assert mt['duration_value_literal'] == '6'
    assert mt['phase'] == 'PHASE_SEQUENCE_ROLE_UNRESOLVED'
    assert mt['phase_hold_reason'] == 'TREATMENT_VS_PRETREATMENT_ROLE_UNRESOLVED'
    assert mt['state'] == 'AUTHOR_METHOD_PHASE_ROLE_REQUIRES_REVIEW'
