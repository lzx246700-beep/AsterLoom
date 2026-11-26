"""Single-model ATP relation candidates, checked against frozen author spans.

This is a review-only building block; it is not a human reviewer or Gold gate.
"""
from pathlib import Path
from . import r44_models
from .r44_arbiter import validate
from .r44_packets import verify_packet


def reason(packet_path: Path, packet_sha256: str, *, model: str = 'deepseek-flash',
           allow_network: bool = False, allow_billed_remote: bool = False,
           allow_source_disclosure: bool = False, response_journal: Path | None = None) -> dict:
    packet = verify_packet(Path(packet_path), packet_sha256)
    output = r44_models.call(r44_models.Provider('deepseek', model), packet,
        allow_network=allow_network, allow_billed_remote=allow_billed_remote,
        allow_source_disclosure=allow_source_disclosure, response_journal=response_journal)
    try:
        checked = validate(packet, output['answer'])
        status = 'REVIEW_ONLY_NOT_SCIENTIFIC_APPROVAL'
        validation_error = None
    except (ValueError, KeyError, TypeError) as exc:
        checked = None
        status = 'MODEL_OUTPUT_HOLD'
        # R44 validation exception codes are internal, bounded identifiers.
        candidate_code = str(exc).split(':', 1)[0]
        validation_error = candidate_code if candidate_code.startswith('R44_') and len(candidate_code) <= 100 else type(exc).__name__
    return {'schema':'asterloom.atp-experiment-reasoner/0.1',
            'status':status, 'validation_error':validation_error,
            'model_output':output['answer'],
            'source_packet_sha256':packet_sha256, 'provider':output['provider'],
            'model':output['model'], 'usage':output['usage'],
            'budget_reserved_microdollars':output['budget_reserved_microdollars'],
            'checked_candidates':checked, 'independent_gold':0, 'training_approved':0}
