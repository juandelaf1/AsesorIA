# RAG Ops Skill — AsesorIA

## Propósito
Procedimiento reusable para operar, evaluar y desplegar el motor RAG de
AsesorIA (ingesta, evaluación, seguridad, despliegues). Complementa a los
skills de frontend (`frontend-ux`, `accessibility`) sin cruzar la frontera
frontend/backend.

## 1. Inspeccionar antes de modificar
1. `git status` — rama `main`, árbol limpio.
2. Identificar el ámbito: `src/` (motor), `scripts/` (ingesta/eval/ops),
   `.github/`, `Dockerfile`, `railway.json`, `render.yaml`.
3. No tocar `ui/` salvo dependencia de contrato explícita y justificada.

## 2. Corpus e índice
- Ingesta: `python -m scripts.index_corpus` → `chroma_db/corpus_runs/<id>/`
  con `manifest.json` (digest SHA-256 por registro).
- Verificar un run: estado `verified` en el manifest; `in_progress` no sirve
  para evaluar.
- Colecciones por configuración de chunking (p. ej. `corpus_256_32`:
  chunk 256, overlap 32). La de producción es `corpus_256_32`
  (ganadora de la evaluación; `src/common/settings.py` y `render.yaml`).
- El modelo de embeddings del índice debe coincidir con el de consulta
  (actualmente `intfloat/multilingual-e5-base`); cambiar
  sentence-transformers/transformers exige re-validar recuperación.

## 3. Evaluación
1. Retrieval (barato, sin LLM):
   `python -m scripts.evaluate_retrieval --manifest <run>/manifest.json`
   → compara todas las colecciones del run en k∈{3,4,5,8} contra
   `data/eval/benchmark.json` (40 preguntas; 30 con referencia de documento).
2. Generación (consume cuota Groq):
   `MLFLOW_TRACKING_URI=sqlite:///mlflow.db`
   `python -m scripts.evaluate_rag_mlflow --manifest <run> --collection <c> --limit N --judge`
   → doc/page hit, latencias, tokens, coste y juez LLM (faithfulness/relevance 1-5).
3. Acotar siempre `--limit`: la cuota diaria de Groq (TPD) es finita.
   Si responde 429 → estado `BLOCKED` y reintentar en la ventana.
4. Nunca presentar métricas de `ui/mock_rag.py` como evaluación real.
5. Registrar la evidencia en `docs/portfolio/EXECUTION_REPORT.md`.

## 4. Despliegue
- Producción: Railway; redespliega en cada push a `main`.
  `railway.json` descarga el corpus desde HuggingFace si falta el índice.
- Alternativa: Render (`render.yaml`, blueprint con disco persistente).
- Imagen: CI publica en GHCR/Docker Hub desde `main` (job `publish` exige
  test + e2e + docker + docs en verde).
- `/health` es liveness de Chainlit: NO sirve como prueba de que el RAG
  responde. La verificación real es una pregunta contra el motor.
- El contenedor debe arrancar sin corpus (el backend se importa bajo
  demanda en `ui/rag_adapter.py`): así lo valida el smoke de CI.

## 5. Seguridad y operaciones
- Secretos: nunca en repo ni logs; `GROQ_API_KEY` solo en entorno;
  CI usa `gsk_mock_ci_testing_key`; `.env` no trackeado.
- `python -m pip_audit -r requirements.txt` tras cada cambio de
  `requirements.txt`. chromadb 1.x no tiene fix para sus avisos de servidor:
  este despliegue usa el cliente embebido → no exponer jamás un servidor Chroma.
- Backup del SQLite de conversaciones (coherente, con validación):
  `python -m scripts.backup_chainlit_db --output <ruta>`
  Restore: copia manual sobre la ruta activa con la app parada.
- Rate limiting por sesión: `CHAT_RATE_LIMIT` / `CHAT_RATE_LIMIT_WINDOW_S`
  (`ui/rate_limit.py`); ≤ 0 lo desactiva.
- Aviso de privacidad: `ui/formatters.format_privacy_notice()`; debe
  reflejar siempre el flujo real de datos y los proveedores vigentes.

## 6. Estados de informe
Usar `PASS` / `FAIL` / `BLOCKED` / `NOT RUN` con comando + commit en
`docs/portfolio/EXECUTION_REPORT.md`. `BLOCKED` para dependencias externas
accesibles solo por el usuario (dashboard Railway, cuota Groq, credenciales).
