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
| P1 | E2E en CI + smoke de contenedor | `PASS` (commit `2371679`, run #7) |
| P2 | Evaluación RAG (recuperación + generación) e informe de colección | `PARTIAL` — recuperación completa; generación 1/2 muestras (cuota Groq) |
| P3 | Backup/restore SQLite, dependencias, rate limiting, aviso de privacidad | `PASS` (volumen Railway `BLOCKED`) |
| P4 | Tests conversacionales extremo a extremo | `PASS` (E2E multi-turno `524ce89`; muestra Groq automatizada `BLOCKED` por cuota) |
| P5 | `AGENTS.md`/skills, README e informe final | `NOT RUN` |
| FASE FINAL | Revisión de release | `NOT RUN` |

---

## 7. P1 — E2E y smoke de contenedor en CI (`2371679`, run #7)

| Job | Contenido | Resultado |
|---|---|---|
| `test` (3.11 y 3.13) | pytest completo | `success` (2,2 / 2,5 min) |
| `e2e` (nuevo) | Python 3.13 + Chromium (`playwright install --with-deps`), `E2E=1 pytest tests/e2e -q` con clave mock, artefactos en fallo | `success` (2,7 min) |
| `docker` | build con `load: true` + **smoke**: arranque, `/health` con reintento acotado (36×5 s), logs al fallar, limpieza `if: always()` | `success` (1,6 min) |
| `docs` | build de documentación | `success` |
| `publish` | publicación de imagen; ahora exige `e2e` además de `test`/`docker`/`docs` | `success` |

El smoke del contenedor valida el arranque sin corpus (el backend se importa bajo demanda); `/health` es liveness de Chainlit, no prueba RAG.

---

## 8. P2 — Evaluación RAG (run local `ae121e06…`, corpus verificado)

Benchmark: 40 preguntas revisadas contra el corpus real (30 con referencia de documento; 28 con referencia de página).

### 8.1 Recuperación — `scripts/evaluate_retrieval.py` (3 colecciones × k∈{3,4,5,8})

| Colección | Doc hits @3 | Página @8 | Tokens medios @8 |
|---|---|---|---|
| `corpus_256_32` | **30/30** | 24/28 | **1 944** |
| `corpus_384_48` (actual en prod, provisional) | 29/30 | 24/28 | 2 964 |
| `corpus_480_64` | **30/30** | 24/28 | 3 740 |

- Fallo documental @8: solo `corpus_384_48` (q15, base mínima tramo 1); `256_32` y `480_64`: 0 fallos.
- Resultados crudos: `chroma_db/corpus_runs/ae121e06…/evaluations/20261013T1149…` (fuera de git).
- Lectura: `corpus_384_48` es la **peor** de las tres (menos doc hits, ~50 % más tokens que `256_32`). `corpus_256_32` domina en coste con la misma cobertura documental que `480_64`.

**Decisión (`e4e7697`)**: la app por defecto ya apuntaba a `corpus_256_32` (`src/common/settings.py:36`), y Railway (producción) la usa. Solo `render.yaml` la sobreescribía con la peor (`corpus_384_48`); el blueprint queda alineado con la evidencia. Confirmación de generación pendiente de la cuota Groq (§8.2); no bloquea porque la colección solo afecta al retrieval y la cobertura documental de `256_32` es igual o mejor.

### 8.2 Generación — `scripts/evaluate_rag_mlflow.py --limit 12 --judge` (Groq real)

Muestra: primeras 12 preguntas · modelo `openai/gpt-oss-120b` · juez LLM (faithfulness/relevance 1-5).

| Colección | Doc hit | Página hit | Faithfulness | Relevance | Latencia media | Coste/consulta |
|---|---|---|---|---|---|---|
| `corpus_384_48` | 12/12 | 10/12 | 3,17/5 | 3,75/5 | 26,4 s (gen 23,9 s) | $0,002 |
| `corpus_256_32` | — | — | — | — | — | `BLOCKED` (cuota diaria Groq) |

- `BLOCKED` momentáneo: cuota TPD de Groq (200 k tokens/día) agotada durante la segunda muestra; reintentar tras el reinicio de ventana.
- El juez tuvo 3 esperas por rate limit (30-60 s); sin errores 429 definitivos en la muestra 1.
- MLflow local (`sqlite:///mlflow.db`) registra tokens, coste y trazas.

---

## 9. P3 — `PASS` (salvo volumen Railway, `BLOCKED`)

### 9.1 Backup/restore del SQLite de conversaciones — `PASS` (`b0ec366`)

- `scripts/backup_chainlit_db.py`: backup API de SQLite (coherente con escrituras concurrentes), nunca sobrescribe destino, valida `integrity_check` + recuento de tablas, `chmod 0600` en posix. Restauración documentada como copia manual con la app parada.
- Tests: `tests/test_backup_chainlit_db.py` (5 tests) + ejecución real sobre la base local (20 hilos, 135 pasos, 196 KB).

### 9.2 Dependencias (`pip-audit`) — `PASS` con residual documentado (`f76e830`)

- Antes: 15 avisos en 3 paquetes. Fix: `sentence-transformers>=5.6,<7` (cierra CVE-2026-68770, CVSS 9,8) — el entorno ya validaba ST 6.0.0 + transformers 5.15.1; suite completa 571 passed.
- Después: **5 avisos, todos en `chromadb` 1.5.9** (última versión publicada, sin fix). Todos afectan al **servidor** HTTP de Chroma (inyección pre-auth, RBAC multi-tenant, cruce de tenants). Este despliegue usa solo `PersistentClient` embebido en un proceso único, sin listener HTTP → exposición limitada al perfil embebido. Revisar en el próximo release de chromadb.
- Ojo: requirements con `>=` sigue sin pins exactos (deriva posible); documentado, no bloquea.

### 9.3 Pendiente en P3

| Tarea | Estado |
|---|---|
| Volumen Railway | `BLOCKED` (sin acceso al dashboard) |
| Rate limiting por sesión/IP | `PASS` (`6d414c3`) — 10 msg/min por sesión, ventana deslizante, env-tunable, aviso amable en español; 9 tests |
| Aviso de privacidad en la UI | `PASS` (`ddd0c2b`) — botón **Privacidad** en la bienvenida con copy llano (qué datos, Google, Groq, no entrenamiento, no cesión, derechos); 4 tests |

---

## 10. P4 — Tests conversacionales extremo a extremo — `PASS` (`524ce89`)

- **E2E navegador**: nuevo `test_two_questions_in_a_row_both_get_answered` — dos preguntas seguidas, dos respuestas mock, composer usable; además fija que el throttle de sesión (P3.5) no corta una conversación con ritmo humano (6/6 E2E).
- **Unitario (ya existente, verificado)**: 24 tests de conversational RAG (rewriting, ventana de 20 turnos, no mutación del historial, fallos del rewriter sin inventar), 18 de transporte de historial por el adapter y reenvío engine→pipeline.
- **LLM real multi-turno**: validado manualmente el 2026-10-08 (`data/eval/manual_smoke.md` §B: cadenas B1/B2 con encadenamiento y cálculo). La réplica automatizada con Groq real queda `BLOCKED` por la cuota diaria (§8.2); se ejecutará al abrir la ventana.
