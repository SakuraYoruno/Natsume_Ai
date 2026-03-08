# TODO 02 - Split Decoupling (Retrieval + Integrations + Edge)

## Goal
- [ ] Split large retrieval/integration/edge files to <= 600 lines.
- [ ] Isolate routing config, provider runtime, and fallback logic.

## Retrieval Track
- [ ] `host_brain/retrieval/recall_fusion.py`:
  - [ ] Split config loading/coercion, scoring, post-processing, sqlite boosting.
- [ ] `host_brain/retrieval/reranker.py`:
  - [ ] Split route config, provider registry, fallback scoring.
- [ ] `host_brain/retrieval/embedding.py`:
  - [ ] Split route resolution, provider invocation, vector normalization.

## Integrations + Infra Track
- [ ] `host_brain/integrations/llm_gateway.py`:
  - [ ] Separate provider invocation, normalization, stream assembly.
- [ ] `host_brain/bond/neo4j_client.py`:
  - [ ] Separate config parser, client implementation, evidence normalization.
- [ ] `host_brain/governor/rate_limit_store.py`:
  - [ ] Separate store adapters (memory/sqlite/redis) into distinct modules.
- [ ] `host_brain/memory/canon_world_store.py`:
  - [ ] Separate hash/normalize utilities and storage operations.

## Edge Track
- [ ] `edge_client/senses/asr.py`:
  - [ ] Separate capture pipeline, hotword cache, HTTP fetchers.
- [ ] `edge_client/actuators/tts_engine.py`:
  - [ ] Separate model loading, synthesis adapter, playback pipeline.
- [ ] `edge_client/actuators/vts_driver.py`:
  - [ ] Separate connection/auth/reconnect command paths.

## Done Criteria
- [ ] All files in this track <= 600 lines.
- [ ] Runtime contracts and event schemas unchanged.
- [ ] Provider fallback behavior unchanged in regression checks.
