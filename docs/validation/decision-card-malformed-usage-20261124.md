# Decision-card malformed response accounting (2026-11-24)

Later-employer Gaobao Harness/Eval follow-up. A returned response with missing, malformed or non-object JSON, or a missing/non-string action discriminator, is blocked with `planner_response_invalid`; valid provider-reported usage is preserved. The independent redacted trace validator accepts that terminal failure, and the regression checks zero tool execution and no raw response persistence. Forbidden tools, repeated calls, wrong argument types, premature finish and unsafe abstention also return the same fixed invalid decision, retaining reported usage without tool execution or raw output. Transport failures or missing provider usage remain separately blocked. The tests exercise both malformed and forbidden responses through the synthetic SQL harness. No real provider calls or business success claims.

Validation: `pytest -q tests/test_decision_card_model_planner.py tests/test_decision_card_trace_eval.py` (CI required).
