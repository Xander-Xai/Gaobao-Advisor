# Synthetic business model evaluation: failure triage

## Scope

`benchmarks/model_eval_triage.py` is an offline postprocessor for an existing `SYNTHETIC_DECISION_CARD_V1` model-mode evaluation artifact. It does not call any model, change the online routing graph or certify admissions outcomes.

The project contains two distinct paths: a LangGraph business graph serving the demo application and a separate opt-in constrained tool loop. Successful deterministic SQL baseline checks cannot be described as independent model reasoning.

## Workflow

```bash
# Deterministic synthetic baseline, no provider calls.
python -m benchmarks.decision_card_business_eval --out /tmp/synthetic-baseline.json

# Opt-in: requires your own provider account/key, permission and budget review.
python -m benchmarks.decision_card_business_eval --mode model \
  --model YOUR_MODEL_ID --permit-external-model --max-cases 8 \
  --out /tmp/synthetic-model.json

# Only a complete model-mode artifact is valid input here.
python -m benchmarks.model_eval_triage \
  --report /tmp/synthetic-model.json --out /tmp/synthetic-triage.json

pytest -q tests/test_model_eval_triage.py
```

## Output and evidence boundary

The triage report separates frozen development and holdout populations, named failed cases, failure codes, safe abstentions and model-reported Token totals. It first checks the existing model-run comparison contract to reject deterministic baseline submissions and malformed/incomplete data.

Metadata from an artifact is **self-declared provenance**, not independent provider attestation. This tool sets `provenance_independently_verified=false`, `production_deployment_authorized=false` and `real_admissions_accuracy=NOT_MEASURED`.

The eight-case fixture is small and synthetic. Human admissions expert review, appropriately licensed real-world data, repeated provider trials and production acceptance remain separate requirements.
