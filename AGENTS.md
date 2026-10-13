## Project
AsesorIA — copiloto fiscal RAG para autónomos en España.

## Arquitectura
Usuario
→ Chainlit (ui/app.py)
→ ui/rag_adapter.py (único punto de integración frontend/backend)
→ src/rag/engine.py → src/rag/pipeline.py → retriever (Chroma) + LLM (Groq)
Persistencia de conversaciones: ui/persistence.py (data layer SQLite de Chainlit; CHAINLIT_SQLITE_PATH).
Ingesta: scripts/index_corpus.py → chroma_db/corpus_runs/<id>/ (manifest con digest por registro).
Despliegue: Dockerfile → GHCR/Docker Hub → Railway (producción; redespliega en cada push a main) / Render (blueprint).
`/health` lo sirve Chainlit: es liveness, NO una prueba de que el RAG funcione.

La UI NO debe conocer:
- Chroma
- embeddings
- retriever
- prompts
- LLM
- detalles internos de ingestion

## Ámbitos

### Backend / infra (src/, scripts/, .github/, Dockerfile, railway.json, render.yaml)
SÍ: ingestion, chunking, embeddings, vectorstore, retrieval, prompts, engine,
evaluación RAG, observabilidad (MLflow), Docker, CI/CD, despliegues, backups,
corpus/indexación.
NO: tocar ui/ salvo una dependencia de contrato frontend/backend explícita y justificada.

### Frontend — Persona 4 (ui/, tests frontend)

SÍ:
- ui/app.py
- ui/contracts.py
- ui/formatters.py
- ui/rag_adapter.py
- ui/mock_rag.py
- ui/rate_limit.py
- ui/guidance.py
- ui/persistence.py
- ui/public/
- tests frontend
- documentación UX/frontend
- Playwright
- axe
- Lighthouse
- accesibilidad
- responsive
- microcopy
- diseño visual
- aviso de privacidad

NO:
- ingestion
- chunking
- embeddings
- Chroma
- retrieval
- prompts
- LLM
- RAG engine
- persistencia backend
- autenticación backend
- Docker
- hosting
- infraestructura
- corpus/indexación

No modificar src/ salvo que exista una dependencia de contrato frontend/backend explícitamente necesaria y claramente justificada.

## Framework
Mantener Chainlit.

NO migrar a:
- Angular
- React
- Vue
- Svelte
- otro framework web

No reescribir la aplicación.

## WebMCP
NO implementar WebMCP.
NO añadir APIs experimentales de browser agents.
NO introducir MCP runtime en la aplicación.

## Modern Web Guidance
Puede utilizarse como guía del coding agent.

NO convertir Modern Web Guidance en dependencia runtime.
NO añadirlo a requirements.txt/package.json salvo que exista una necesidad real y explícitamente aprobada.

## Diseño
AsesorIA debe seguir pareciendo:
- asistente RAG corporativo
- interfaz conversacional
- profesional
- limpia
- intuitiva
- accesible
- responsive

NO convertirlo en:
- dashboard
- ERP
- portal administrativo
- NotebookLM
- SaaS sobrecargado

No añadir componentes solo porque estén de moda.

Cada elemento nuevo debe justificar:
1. problema UX
2. beneficio
3. coste de complejidad

## Mock
El mock debe ser siempre claramente un mock.

No:
- inventar scores
- fingir fuentes reales
- fingir conexión con corpus real
- ocultar que es demo

## Backend integration
El único punto de integración frontend/backend es el adapter.

El frontend debe consumir el contrato.
No implementar backend dentro de ui/.

## Tests
Unitarios + integración (cualquier cambio):
python -m pytest tests/ -q
Objetivo: 0 fallos.

E2E (Playwright + axe, instancia mock autónoma, sin secretos):
$env:E2E='1'; python -m pytest tests/e2e -q

CI (.github/workflows/ci.yml), todo debe ir verde antes de confiar en un push:
- test (Python 3.11 y 3.13)
- e2e (Chromium; sube screenshots si falla)
- docker (build + smoke: arranque del contenedor y `/health` con reintento acotado)
- docs
- publish (exige los cuatro anteriores)

Cuando proceda:
- Playwright
- axe
- Lighthouse
- smoke tests
- responsive tests

No modificar tests existentes para hacerlos pasar: si un cambio rompe un test, el cambio es el que está mal.

## Evaluación RAG
- Benchmark: data/eval/benchmark.json (40 preguntas verificadas contra el corpus real).
- Retrieval (compara colecciones): python -m scripts.evaluate_retrieval --manifest chroma_db/corpus_runs/<id>/manifest.json
- Generación + coste + juez LLM: MLFLOW_TRACKING_URI=sqlite:///mlflow.db; python -m scripts.evaluate_rag_mlflow --manifest <manifest> --collection <nombre> [--limit N] [--judge]
- Colección en producción: corpus_256_32 (ganadora de la evaluación; evidencia en docs/portfolio/EXECUTION_REPORT.md §8). Cambiarla solo con evidencia nueva.
- Nunca presentar resultados de mocks como evaluación del motor real.
- La cuota diaria de Groq es finita: acotar muestras (--limit) y registrar el estado BLOCKED si no alcanza.

## Operaciones y seguridad
- Backup del SQLite de conversaciones: python -m scripts.backup_chainlit_db --output <ruta> (no sobrescribe; valida la copia; restore manual con la app parada).
- Rate limiting de sesión: CHAT_RATE_LIMIT / CHAT_RATE_LIMIT_WINDOW_S (ui/rate_limit.py); <= 0 desactiva.
- Aviso de privacidad: ui/formatters.py format_privacy_notice + botón en la bienvenida; actualizarlo si cambia el flujo de datos o los proveedores.
- Secretos: nunca en el repo ni en logs. `.env` local no trackeado (solo `.env.example`). CI usa la clave mock gsk_mock_ci_testing_key. GROQ_API_KEY vive solo en el entorno.
- Dependencias: tras tocar requirements.txt ejecutar python -m pip_audit -r requirements.txt. chromadb va sin fix conocido en 1.x: este despliegue usa el cliente embebido; NO exponer nunca un servidor Chroma.
- El modelo de embeddings del índice debe coincidir con el de las consultas; no cambiar sentence-transformers/transformers sin re-validar recuperación contra el corpus indexado.

## UX rules
Priorizar:

primera visita
→ documentos
→ pregunta natural
→ loading
→ respuesta
→ fuentes
→ nueva pregunta

No forzar keywords.
No añadir otro LLM/classifier para clasificar preguntas.
Las ayudas deben ser deterministas y sencillas.

## Copy
Todo texto visible debe ser:
- español natural
- profesional
- claro
- no técnico
- no repetitivo

Nunca mostrar al usuario:
- stack traces
- mensajes internos
- workarounds
- detalles del agente
- mensajes de implementación
- "Usado"
- mensajes técnicos que no aporten valor

## Git
Trabajo directo sobre main (repo personal, un solo desarrollador).

Antes de cambios:
git status

Commits:
- pequeños y por cambio lógico
- mensaje en inglés, estilo conventional (feat/ fix/ test/ docs/ chore/ ci/)
- nunca commits gigantes ni reescrituras innecesarias
- no force-push

Antes de finalizar:
git diff
python -m pytest tests/ -q (y E2E cuando el frontend cambie)

Railway redespliega en cada push a main: agrupar cambios por fase y verificar CI antes de empujar.
docs/portfolio/EXECUTION_REPORT.md es el registro vivo de la evidencia por fase.
