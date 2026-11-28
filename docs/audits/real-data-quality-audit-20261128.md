# Local real-data quality audit — 2026-11-28

This report records a read-only audit of a locally retained historical
admissions snapshot. It is **not** an authorization, source verification, or
production admissions-quality claim.

## Scope and provenance

- Audited base: `origin/master` at `8b636db`.
- Source: a local historical SQLite snapshot; its full file digest, path, and
  row-level artifacts remain in the access-controlled local validation
  workspace, not in Git.
- Provenance classification: `UNVERIFIED_RIGHTS_THIRD_PARTY`.
- Issue #58 records owner-reported collection from public sources but says
  redistribution rights were not independently verified. This dataset is not
  authorized E3 evaluation data.
- No source rows, names, admissions records, conversation text, or credentials
  are included here. The snapshot's personal conversation tables were not
  transferred to the isolated evaluation database.
- External model transfer and redistribution are not authorized by this audit.

## Coverage

| Asset | Rows / coverage |
|---|---:|
| `admission_scores` | 400,194 rows; 2022–2025; 31 source provinces |
| `schools` | 3,020 institutions |
| `majors` | 215 majors across 22 categories |
| `yi_fen_yi_duan` | 66,420 rows; 30 provinces; 2022–2025 |
| `enrollment_plans` | 14,665 rows; 2024–2025 only; 426 institutions |
| `subject_rankings` | 210 rows |
| `career_trend` | 70 rows |

The tables include both school-level and major-level admission rows. The
province field in `admission_scores` and `enrollment_plans` is the candidate's
source province, not the institution's home province.

## Field availability

| Field | Populated | Rate |
|---|---:|---:|
| `min_score` | 400,194 / 400,194 | 100.00% |
| `min_rank` | 390,977 / 400,194 | 97.69% |
| `plan_count` in admission history | 83,676 / 400,194 | 20.91% |
| `avg_score` | 82 / 400,194 | 0.02% |
| `max_score` | 0 / 400,194 | 0.00% |

Consequently, average and maximum admission scores cannot be asserted from
this snapshot. Candidate cases must mark those fields unavailable rather than
impute them. Major-level admission history exists only for 2024; cross-year
major-level comparison is not supported by this snapshot.

## Integrity and interpretation findings

- No orphan school or major references were found in admission history or
  enrollment plans.
- No duplicate major-level admission keys were found. The 70,216 repeated
  natural-key groups are all school-level rows with `major_id IS NULL`; SQLite
  treats NULL values as distinct in UNIQUE constraints. Queries must keep
  school-level and major-level facts separate.
- 182 admission scores and 140 one-score-one-rank scores exceed 750; all are
  from Hainan's 900-point scale, not ordinary 750-point scores.
- The one-score-one-rank table has 227 cumulative-count increases at score
  segment boundaries, 8,652 score-band gaps, and 582 province/year/track
  combinations without corresponding admission-history rows.
- Admission average and maximum score coverage is insufficient for business
  scoring. Enrollment plans are sparse and absent for 2022–2023.
- The table's subject-track labels vary by province and year. A track-aware
  lookup must prefer an exact track and use a documented legacy mapping only
  when that exact track is absent.

## Isolated ETL and query cross-check

A private ETL copy was built with the current application schema, explicit
third-party/unverified provenance labels, and empty conversation/highlight
tables. The original snapshot was not modified. A separate SQL oracle was
compared with the application query layer over the isolated copy:

- Admission minimum scores: 60/60 matched.
- Enrollment plan totals: 60/60 matched.
- Track-aware rank queries: 15/15 matched across 5 province/year/track groups.
- Match-school source-bound checks: 2/2 passed.
- Total: 137 checks, 0 mismatches after the track-selection correction.

The isolated database's manifest and full evidence are private local files.
These checks validate internal query agreement only; they do not validate
source accuracy, licensing, real-user task success, or model quality.

## External acceptance remains blocked

This audit does not close Issues #104, #147, or #204. Real provider/model
authorization, an independent human-reviewed gold set, verified data rights,
and authorized business UAT remain outstanding. The local snapshot is useful
for read-only quality analysis, but cannot be used to claim authorized real-
data acceptance.
