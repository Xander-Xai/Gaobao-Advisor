# Contributing

Contributions are welcome for engineering quality, safety and
reproducibility. Contributions that reintroduce restricted content are not.

## Hard constraints

1. **No real data.** Everything shipped must be synthetic or demonstrably
   redistributable. `tests/test_public_demo_acceptance.py` enforces this against
   git-tracked files and must stay green.
2. **No unlicensed third-party content.** Do not add quotation corpora,
   knowledge bases, prompt libraries or personas you cannot show permission for.
   The upstream private project's corpus and a named individual's methodology /
   quotation assets were removed for this reason and must not be restored.
3. **Disabled means disabled.** If a subsystem is unavailable, expose that
   state rather than returning an empty-but-successful result.
   See `server/services/rag.py` and `tests/test_g9_retrieval.py`.
4. **Fail closed on safety assets.** Removing `skills/gaokao/safety_rules.md`
   must disable answer generation, not silently drop the rules.
5. **No outbound scraping by default.** `legacy/gaokao_data.py` must not call a
   third-party site unless `ENABLE_OUTBOUND_FETCH` is explicitly enabled.

## Development

```bash
pip install -e ".[dev]"            # or: pip install -r requirements.txt -r requirements-dev.txt
python -m scripts.seed_demo_data --db data/demo.db --reset
pytest tests -q --no-header
ruff check . && ruff format --check .

cd frontend && npm ci && npx vitest run && npx vite build
```

The optional Java services have their own build and unit tests:

```bash
mvn -f java-services/pom.xml -B -ntp verify
```

The runtime dependency set is `requirements.txt` / `requirements.lock`; it must
have **zero known vulnerabilities** and CI enforces it with `pip-audit`. Do not
add a dependency to it without running `make audit-deps` — and check
[`docs/dependency-exceptions.md`](docs/dependency-exceptions.md) first, because
some packages are deliberately kept out of the runtime (see the policy there).

The development dependency set is locked in `uv.lock` and pinned by
`[tool.uv] constraint-dependencies` in `pyproject.toml`; the frontend pins the
same way with `npm overrides`. Those floors keep a plain re-resolution from
returning a package to an advisory range — change them deliberately, then
re-run the audit and the tests.

The optional legacy Streamlit operations dashboard has its own dependency file
and is never installed by default:

```bash
make install-legacy-admin   # pip install -r requirements-legacy-admin.txt
make run                    # streamlit run legacy/admin.py
```

CI runs on Python 3.10 and 3.11, plus a frontend gate, a secret scan, a
no-key container smoke test and a redistribution gate. The `Java Services`
workflow (`java-unit-tests` + `java-compose-smoke`) runs when Java, overlay or
benchmark files change, and CodeQL additionally analyses `java-kotlin`.

The reproducible load benchmark lives in the repository and is not a claim about
production capacity:

```bash
python benchmarks/overview_perf_bench.py --seconds 30 --rounds 3
```
