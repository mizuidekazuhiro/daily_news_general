"""Use the shared structured client while keeping validation logs free of feed data."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory


VALIDATION_CODES = {
    "event_pair_ids", "event_pair_shape", "unsupported_event_anchor", "model_refusal",
    "summary_evidence_count", "summary_sections", "summary_story_ids", "unknown_summary_evidence",
    "response_not_completed", "schema",
}


def request_batch(client, model, *, phase, prompt, payload, schema, validate, max_output_tokens):
    with TemporaryDirectory(prefix="newspaper-briefing-ai-") as folder:
        path = Path(folder) / "response.json"
        try:
            response = client.generate_structured_json(
                model=model, system_prompt=prompt, user_prompt=json.dumps(payload, ensure_ascii=False),
                max_output_tokens=max_output_tokens, temperature=0, schema=schema,
                validate=validate, diagnostics_path=path,
            )
            return validate(response), "ok"
        except Exception as error:
            attempts = json.loads(path.read_text(encoding="utf-8")).get("attempts", []) if path.is_file() else []
            errors = [error for attempt in attempts for error in attempt.get("validation_errors", [])]
            if not errors and isinstance(error, (ValueError, TypeError, KeyError, IndexError, AttributeError)):
                errors = [str(error)]
            if errors:
                codes = {value.partition(":")[0] for value in errors}
                reasons = codes & VALIDATION_CODES
                if codes - VALIDATION_CODES:
                    reasons.add("other_validation")
                print(f"Briefing AI {phase}: {', '.join(sorted(reasons))}")
                return None, "invalid"
            print(f"Briefing AI {phase}: unavailable")
            return None, "unavailable"
