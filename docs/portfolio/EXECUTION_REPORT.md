# Informe de ejecución — AsesorIA portfolio-ready

> Informe vivo. Cada bloque se actualiza al terminar la fase correspondiente.
> Estados: `PASS` / `FAIL` / `BLOCKED` / `NOT RUN`.

- **Fecha de inicio**: 2026-10-13
- **Commit de referencia**: `8a2f17f` — *Merge pull request #1 from juandelaf1/feature/frontend*
- **Rama de trabajo**: `main` (sin PRs; commits directos con suite verde)

---

## 1. Estado inicial del repositorio (FASE 0)

| Comprobación | Resultado |
|---|---|
| Rama al empezar | `feature/frontend` @ `121c3db`, sincronizada con `origin/feature/frontend` |
| `main` local | `f81295b` (atrasado 6 commits respecto a `origin/main`) |
| `main` remoto | `8a2f17f` = merge del PR #1 (CI verde en ese push, verificado vía API pública) |
| Alineación | `git checkout main && git merge --ff-only origin/main` → fast-forward limpio a `8a2f17f` |
| Árbol | Limpio al inicio y al final de la fase (`git status -sb` sin cambios) |
| PRs abiertos | Ninguno (API pública) |
| Ramas locales eliminadas | `feature/frontend`, `feat/conversational-rag` — ambas verificadas con `git log origin/main..<rama>` vacío (sin trabajo exclusivo) |
| Rama conservada | `docs/railway-deploy` (NO fusionada en `origin/main`; contiene trabajo potencial exclusivo) |
| Ramas remotas | No se han eliminado ni modificado |

**Nota sobre `8a2f17f`**: sigue siendo el HEAD de `origin/main` y el commit correcto de referencia; es el desplegado en producción (Railway despliega desde `main`).

---

## 2. Arquitectura observada (verificada en código, no de documentos)

```
Usuario → Chainlit (ui/app.py)
          → ui/rag_adapter.py  (único punto de integración; normaliza
            historial, ventana de 20 turnos, timeout RAG_BACKEND_TIMEOUT_S)
          → src/rag/engine.py → src/rag/pipeline.py
                               → src/retrieval/retriever.py (Chroma)
                               → LLM (Groq vía langchain-groq)
          → ui/persistence.py (data layer SQLite + LocalStorageClient
            para elementos; CHAINLIT_SQLITE_PATH)
          → src/common/tracing.py (MLflow, off por defecto)
```

- **Ingesta**: `scripts/index_corpus.py` → `src/ingestion/*` → `src/indexing/*` → Chroma en `chroma_db/corpus_runs/<id>/`.
- **Despliegue**: `Dockerfile` (python:3.13-slim, usuario no root, modelo de embeddings horneado) → CD publica imagen en GHCR/Docker Hub desde `main` → Railway (`railway.json`, arranque con descarga del corpus desde HuggingFace si falta el índice) / Render (`render.yaml`, blueprint con disco persistente).
- **Arranque sin corpus**: `ui/app.py` no importa `src/` ni abre Chroma al arrancar; el backend se instancia bajo demanda (`ui/rag_adapter.py:26,196`) → la imagen puede dar `/health` sin corpus.
- **`/health`**: lo sirve Chainlit (`chainlit/server.py:2186`), no hay endpoint propio. Es liveness, **no** prueba de que el RAG funcione.

---

## 3. Inventario de pruebas

| Suite | Comando | Nº |
|---|---|---|
| Unitarias + integración | `python -m pytest tests/ -q` | 36 ficheros, 466 funciones de test |
| E2E (Playwright + axe) | `E2E=1 python -m pytest tests/e2e -q` | 5 tests (smoke, recarga, axe, responsive) |
| En CI (`.github/workflows/ci.yml`) | solo `pytest tests/` en 3.11 y 3.13 + build Docker + build docs | **E2E y smoke de contenedor NO ejecutados en CI** (P1) |

Requisitos E2E verificados en local: Chromium de Playwright instalado; el conftest levanta su propia instancia mock (OAuth forzado a vacío) y no necesita servicios externos.

---

## 4. Resultados de la línea base (P0)

Fecha: 2026-10-13 · commit `8a2f17f` · Windows, Python del entorno local (miniconda).

| # | Comando | Resultado | Estado |
|---|---|---|---|
| 1 | `python -m pytest tests/ -q` | **566 passed, 10 skipped, 61 warnings** en 115 s | `PASS` |
| 2 | `$env:E2E='1'; python -m pytest tests/e2e -q` | **5 passed** en 33 s | `PASS` |
| 3 | `GET https://asesoria.up.railway.app/health` | `200` · `{"status":"ok"}` (15 B) | `PASS` |
| 4 | `GET https://asesoria.up.railway.app/` | `200` · HTML de arranque (3558 B) | `PASS` |
| 5 | Carga en navegador (Chromium, solo lectura) | `200`, título *Asesor Fiscal IA*, botón **Google** presente (login OAuth configurado), sin peticiones fallidas de red | `PASS` |
| 6 | Consola del navegador en pre-login | 4 respuestas `401` de endpoints de sesión de Chainlit (comportamiento esperado sin sesión) | `PASS` (observación) |

**Limitaciones del smoke (no confundir con funcionalidad RAG):**

- `/health == 200` confirma que el proceso HTTP está vivo; **no** verifica corpus, retrieval, generación ni persistencia.
- El composer no es accesible sin autenticación: **no se verificó** (y no se intentará eludir OAuth) una conversación real, la recuperación de historial ni las citas en producción.
- No se guardaron cookies, tokens ni sesiones; la captura de pantalla es de la pantalla pública de pre-login y vive fuera del repositorio.

---

## 5. Riesgos y dependencias externas

| Riesgo / dependencia | Estado |
|---|---|
| Volumen persistente en Railway (SQLite de conversaciones + corpus) | `BLOCKED` — sin acceso al dashboard; `railway.json` no declara volumen (Render sí lo hace en `render.yaml`). Verificación manual: Railway → servicio → tab *Volumes* |
| `GROQ_API_KEY` | Disponible en el entorno local (no en el repo, no en `.env`); CI usa `gsk_mock_ci_testing_key` |
| Colección Chroma `corpus_384_48` | Provisional, sin ganadora validada (declara `render.yaml`) → FASE P2 |
| `railway.json` fija `CHROMA_DIR` a un run concreto y descarga el corpus desde HuggingFace en cada arranque sin índice | Acoplamiento a red externa; candidato a documentar/mejorar |
| Credenciales `gh` (GitHub CLI) | `BLOCKED` (HTTP 401) → verificación de CI vía API pública de GitHub |
| Dependencias sin pin exacto (`requirements.txt` con `>=`) | Riesgo de deriva; revisar en P1.3 |
| MLflow local (`mlruns/`, `mlflow.db`) | Almacenamiento efímero en despliegues sin volumen |

---

## 6. Plan de ejecución adaptado

| Fase | Contenido | Estado |
|---|---|---|
| FASE 0 | Inspección + línea base | `PASS` |
| P0 | Alineación, suites, smoke producción | `PASS` |
| P1 | E2E en CI + smoke de contenedor + calidad de workflow | `NOT RUN` |
| P2 | Evaluación RAG (recuperación + generación) e informe de colección | `NOT RUN` |
| P3 | Backup/restore SQLite, dependencias, rate limiting, aviso de privacidad | `NOT RUN` |
| P4 | Tests conversacionales extremo a extremo | `NOT RUN` |
| P5 | `AGENTS.md`/skills, README e informe final | `NOT RUN` |
| FASE FINAL | Revisión de release | `NOT RUN` |
