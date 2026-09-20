# PharmaWatch

Drug safety signal platform over the entire FAERS quarterly report catalog. Detects disproportionate reporting between drugs, reactions, and demographics using PRR and ROR. Built for clinicians, health journalists, and analysts.

## Architecture

```
openFDA quarterly extracts
  → raw Parquet (immutable, Cloudflare R2)
  → DuckDB (dedup, typing, drug name normalization)
  → dbt-duckdb marts (PRR/ROR) → MotherDuck
  → FastAPI + RAG (planned)
```

## Ingestion pipeline

Seven standalone modules run in sequence, each reading the previous stage's output:

| Module | What it does |
|---|---|
| `download.py` | Fetches quarterly ZIP extracts from openFDA |
| `parse.py` | Extracts raw `$`-delimited text files into Parquet, handling FAERS formatting defects |
| `schema.py` | Maps each era's column names to one canonical schema (FAERS changed layouts multiple times) |
| `clean.py` | Applies canonical renames, removes null/deleted caseids, deduplicates rows within a quarter |
| `merge.py` | Unions all quarters per table into single Parquet files |
| `dedup.py` | Case-version deduplication across all quarters — keeps only the latest version of each case |
| `validate.py` | Reconciliation gate: five invariants that must pass before data leaves the pipeline |
| `load.py` | Uploads deduped Parquet to R2 as the canonical source of truth |

## dbt

Star schema built with `dbt-duckdb`, materialized into MotherDuck. For detailed schema layout and design rationale, see [docs/star_schema.md](docs/star_schema.md).

Dimension tables cover drugs (name, route), reactions (preferred term), and demographics (age group, sex, reporter country). Outcomes are **not** a dimension — because a single case can have multiple outcomes (e.g. both hospitalization and death), they are pivoted into boolean flags directly on the fact table (`has_death`, `has_hospitalization`, etc.) rather than joined through a foreign key.

The fact table grain is one row per (case, drug, reaction) triple. This isolates each drug-reaction pair for direct use by the PRR and ROR signal detection marts.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env  # fill in R2 credentials and MotherDuck token
```

## Roadmap

**Planned.** Drug names in FAERS are free-text and wildly inconsistent. Normalization against a medical ontology (RxNorm or similar) is next, along with the same treatment for reaction terms. A frontend will give analysts and journalists live query access to the warehouse without writing SQL.

**Exploring.** A FastAPI service layer, RAG combining warehouse stats with drug label text (pgvector), and ML-based signal detection are under consideration.

## Data quality

FAERS data has extensive formatting inconsistencies across its 20+ year history — schema changes, delimiter bugs, embedded characters, partial-precision dates, and more. All documented issues are tracked in [docs/mess_log.md](docs/mess_log.md).

## License

[MIT](LICENSE)
