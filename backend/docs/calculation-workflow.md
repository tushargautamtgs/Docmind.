# Bounded answer workflow

`POST /documents/{document_id}/ask` retains its existing request and response
fields (`document_id`, `question`, `answer`, `retrieved_sources`). Search,
uploads, metadata and indexes are unchanged. Retrieved passages are retrieval
results, not automatically verified answer citations.

The deterministic router sends permission, limit comparison, timeline,
checklist and skills questions to the existing direct model answer path.
A numeric mention alone never invokes extraction. Explicit supported calculation
intents invoke structured extraction; explicit unsupported arithmetic returns an
unsupported-calculation response. No model-authored operations are accepted.

Python constructs exactly two formula types:

- `remaining_balance`: verified entitlement minus verified usage, in days.
- `capped_reimbursement`: minimum(actual, cap) for each category, then sum,
  in one explicitly supported currency. Eligibility/approval are not inferred.

Inputs must include exact quotes, exact number text, source, units and periods.
Whitespace is normalized; spelling/punctuation are not fuzzily matched.
Document provenance is resolved against retrieved text, preferring the declared
chunk when it matches. A wrong ID cannot manufacture evidence; an exact quote
in another actual chunk is cited with that resolved ID. Overlapping duplicate
quotes are valid. Document facts must be document inputs, usage/costs must be
supported by the question. Conservative local role/category checks reject
ambiguous multi-amount quotes. Short self-contained quoted clauses work best.

Supported number words are explicitly enumerated in `calculation_executor.py`:
zero through nineteen, the multiples of ten through ninety, one hundred and
one thousand. Other compound/scale forms are rejected, including truncating an
unsupported word phrase into a smaller supported number. Numeric strings must
be plain nonnegative decimals; source tokens may use correctly grouped commas.
No period or currency conversion is performed. Day/night reimbursement requires
one explicitly approved day/night. Monthly versus annual amounts cannot mix.

Successful calculations are rendered in Python, with units and verified
supporting quotes. They never pass through a model rewrite. Unsupported
calculations, absent verified inputs and malformed/unverifiable model output
have distinct responses. Extraction failure is not evidence of document absence.

## Verification

From the project root:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
```

The fixtures cover A–L, extraction schema validation and FastAPI response
compatibility. The direct-answer fixtures deliberately control model responses:
they verify routing and response plumbing, not real-model comprehension. Model
transport is patched, so unit tests require neither Ollama nor model downloads.

On 2026-10-29, real Ollama `qwen3:1.7b` was reached with approved
execution outside the sandbox, using the existing handbook index and retrieval.
Local unit/contract tests validate plumbing and deterministic checks only.

The initial real extraction emitted only `kind` and `missing_information`, while
its prose described the available inputs. Requiring the routed fields exposed a
second error: both annual inputs were labeled `per_day`. The generation schema
now requires routed fields and limits leave balance dimensions to days and
annual/monthly/weekly periods. Quotes, role checks and deterministic formulas
still validate independently; omitted fields are extraction failure, not proof
of missing evidence. `sick leaves` in question usage is accepted as a days unit.

The latest recorded real run produced the correct verified sick balance, but
**the direct-answer failures remain unresolved**. Carry-forward said no and gave
the five-day limit, but wrongly applied March 31 to the remaining three days and
added sick leave policy. The stolen-device answer included unrelated finance
and travel policy and omitted immediate reporting. The recorded outputs are in
`backend/tests/fixtures/real_model_observed_results.json`; they are failure
fixtures, not approved expected answers. No complete live fix is claimed.

### Run the three real-model cases on a Mac

From the repository root, with Ollama and `qwen3:1.7b` available:

```sh
backend/.venv/bin/python backend/scripts/diagnose_real_model.py
```

This uses real retrieval and Ollama directly, without an API server or mocks.
It defaults to the existing handbook document ID; use `--document-id UUID` for
another saved index. It reads indexes without modifying stored files. A new
owner-only JSONL file in `/tmp` records model name, case, selected route,
retrieved chunk IDs, raw and parsed structured extraction, exact validation
failure, raw direct model response, and final answers. Per-request IDs correlate
model events. Known regression pattern checks produce a nonzero exit status on
failure; passing those checks still requires semantic review. Model/retrieval
errors are recorded for each case and also produce a nonzero exit status.

For opt-in diagnostics in the API process, set `DOCMIND_DIAGNOSTICS_PATH` to a
local absolute file path before starting the backend. These local logs contain
private extracted quotes/model output; keep them local. No diagnostic payload
is added to the API contract. `/ask` search/model errors and request-validation
errors do not echo question or document contents into browser error responses.

## Limits

Routing uses conservative English intent patterns, not a general language
understanding guarantee. Extraction can fail on paraphrases, broad quotes,
implicit units/periods and unsupported quantity forms. The model can omit an
input/category; deterministic provenance checking cannot guarantee extraction
completeness or all semantic interpretations. Multi-day scaling, tax, currency
conversion and other calculations are unsupported. Direct answers still depend
on retrieval and the local model; these tests do not establish that hallucinations
are eliminated. Run representative real handbook /ask requests in an environment
with localhost access before relying on real-model extraction quality.
