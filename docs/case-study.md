# AsesorIA — Technical Case Study

## Problem
Spanish fiscal/normative information for self-employed workers is extensive, technical, and distributed across long official documents. A useful assistant must answer in natural language **without inventing unsupported claims** and must make it easy to verify responses against source documents.

## Decisions
- **RAG-first architecture:** retrieve relevant passages from an indexed corpus before generation (`retrieval first, generation second`).
- **Grounding and traceability:** responses are tied to retrieved context with document/page/source metadata so users can inspect evidence.
- **Abstention over hallucination:** when evidence is insufficient or out of scope, the system is designed to abstain rather than assert unsupported answers.
- **Conversational context handling:** use query rewriting with conversation history (while keeping answer generation grounded in retrieved documents) to resolve follow-up references.
- **Reproducible pipeline components:** ingestion/cleaning/chunking/embeddings/vector store (Chroma), retrieval evaluation, automated tests, and CI workflows.
- **Operational transparency:** configurable persistence, observability/evaluation tooling (e.g. MLflow scripts/workflows where configured), and documented run/deploy paths.

## Trade-offs
- **Groundedness vs coverage:** prioritizes answers supported by the indexed corpus; cannot answer reliably beyond available/updated sources.
- **Quality vs latency/cost:** retrieval depth, rewriting, and generation introduce latency and provider/rate-limit constraints (e.g. LLM API quotas).
- **Pattern-based controls vs perfect safety:** privacy/PII detection and other safeguards are helpful controls but not guarantees of complete detection.
- **Local/demo practicality vs enterprise scope:** the project demonstrates an end-to-end, evaluable RAG system rather than a fully hardened enterprise fiscal platform.

## Evidence
- **Automated tests and CI:** pytest suite and GitHub Actions workflows (tests, E2E/smoke/docs/build steps as configured in repo).
- **Retrieval/generation evaluation assets:** benchmark and evaluation scripts (e.g. retrieval benchmark, MLflow-based evaluation scripts) with documented configurations/results context in README/docs.
- **Traceable responses in UI/runtime:** cited sources/pages and observable behavior (including abstention cases) in documented demos/conversations.
- **Reproducible runtime paths:** local execution, Docker/Compose, and deployment configuration/documentation present in repository.

## Limits & learnings
- **Limits:** depends on corpus freshness, indexing quality, and model behavior; grounding reduces but does not eliminate errors; not a substitute for professional fiscal advice.
- **Learnings:** explicit retrieval evaluation, source traceability, and conservative abstention policies are essential for trustworthy domain RAG; separating UI, adapter, and RAG internals improves maintainability and testing.
