"""Read existing index, run real retrieval + Ollama, record private local diagnostics.
Run from repository root. No API server, writes to documents, or mocks required.
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import UUID

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document-id', default='d4ba513297b842c5a74283f1184057a7')
    parser.add_argument('--top-k', type=int, default=3, choices=range(1, 11))
    parser.add_argument('--output', type=Path, default=Path('/tmp') / ('docmind-real-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.jsonl'))
    args = parser.parse_args()
    identifier = UUID(args.document_id).hex
    # Exclusive creation avoids silently appending to an old run or replacing a file.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    os.environ['DOCMIND_DIAGNOSTICS_PATH'] = str(args.output.resolve())
    from app.services import diagnostics
    from app.services.answer_service import generate_answer, MODEL_NAME
    from app.services.search_service import search_document

    fixtures = json.loads((BACKEND / 'tests/fixtures/observed_failures.json').read_text())
    diagnostics.record('run', model=MODEL_NAME, document_id=identifier, top_k=args.top_k,
                       verification='real Ollama and existing index; no mocks')
    failed = False
    for case in fixtures:
        diagnostics.record('case_start', case=case)
        try:
            matches = search_document(identifier, case['question'], args.top_k)
            answer = generate_answer(case['question'], matches)
            checks = {
                'required': {pattern: bool(re.search(pattern, answer, re.I | re.S)) for pattern in case['required_patterns']},
                'forbidden_absent': {pattern: not bool(re.search(pattern, answer, re.I | re.S)) for pattern in case['forbidden_patterns']},
            }
            passed = all(checks['required'].values()) and all(checks['forbidden_absent'].values())
            failed |= not passed
            diagnostics.record('case_result', case_id=case['id'], answer=answer, checks=checks, passed=passed)
            print(f"{case['id']}: {answer}\n", flush=True)
        except Exception as error:
            failed = True
            diagnostics.record('case_error', case_id=case['id'], error_type=type(error).__name__, detail=str(error))
            print(f"{case['id']}: ERROR {type(error).__name__}; see diagnostic file", flush=True)
    print(f'Diagnostics: {args.output.resolve()}', flush=True)
    # Pattern checks catch known failures; semantic review is still required.
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
