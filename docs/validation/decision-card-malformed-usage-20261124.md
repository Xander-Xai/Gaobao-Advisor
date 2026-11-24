# Decision-card malformed response accounting (2026-11-24)

Later-employer Gaobao Harness/Eval follow-up. A returned response with missing, malformed or non-object JSON, or a missing/non-string action discriminator, is blocked with `planner_response_invalid`; valid provider-reported usage is preserved. The independent redacted trace validator accepts that terminal failure, and the regression checks zero tool execution and no raw response persistence. Explicit forbidden actions remain fail-closed under the existing policy checks; their usage reconciliation is a separate follow-up. No real provider calls or business success claims.

Validation: `pytest -q tests/test_decision_card_model_planner.py tests/test_decision_card_trace_eval.py` (CI required).
