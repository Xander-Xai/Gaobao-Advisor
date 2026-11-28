# Data license and provenance

## Dataset in this repository

`scripts/seed_demo_data.py` generates the entire dataset shipped with this demo.

* **Type:** synthetic, machine-generated, deterministic (seed `20240617`)
* **License:** CC0-1.0
* **Contains:** no real institution, programme, score, rank or policy data
* **Markers:** institution names carry `（合成）`; provinces are invented
  (`示例省·甲`…); the `schools.special_type` column carries
  `SYNTHETIC_DEMO_DATA`

Regenerate with:

```bash
python -m scripts.seed_demo_data --db data/demo.db --reset
```

## Synthetic knowledge corpus

`knowledge/synthetic_demo/` is the knowledge corpus that ships with the demo.

* **Type:** original, machine-authored, synthetic (no source text reused)
* **License:** CC0-1.0
* **Contains:** fictional institution rules, illustrative admission-policy
  examples, volunteer-application FAQ and explanatory notes
* **Markers:** every `groups/*.md` file starts with a `SYNTHETIC_DEMO` notice;
  `manifest.json` records the corpus marker, licence, version and per-file
  sha256 hashes; institution names carry `（合成）` and match
  `scripts/seed_demo_data.py`
* **Not included:** the upstream persona-methodology group, any quotation
  collection, or any real admission policy/data

`server/services/rag.py::validate_corpus` refuses to serve a corpus that does
not carry the `SYNTHETIC_DEMO` marker, so an unmarked or rights-unclear corpus
cannot silently masquerade as demo knowledge.

## Historical commit caveat

The statements above describe the **current tree only**. Issue #58 records a
historical public database object and an accepted-risk owner disposition; that
decision did not grant redistribution rights to the third-party data. During a
live repository check on 2026-11-28, the previously documented commit and raw
content URLs returned 404, and the current default branch and observed remote
branches did not contain the removed database path. This check does not prove
that every external cache or backup has been purged. Nothing in this licence
grants rights to the historical object or to any third-party admissions data.
See [`SECURITY.md`](SECURITY.md) for the current evidence boundary.

## What is deliberately NOT here

The upstream private project contained a knowledge corpus and a quotation
collection attributed to a named individual, plus production database backups.
None of it is redistributed here. See [the project overview](README.md),
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and the
[documentation index](docs/README.md).

If you add a corpus, you must be able to state its licence and provenance and
must enable RAG only after that corpus passes the integrity check.
