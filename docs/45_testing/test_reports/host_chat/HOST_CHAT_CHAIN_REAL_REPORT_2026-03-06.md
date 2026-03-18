受众：MASTER
标题：HOST Real Chain Report (Doc-Aligned Node Order)

# HOST Real Chain Report (Doc-Aligned Node Order)

- Generated At: 2026-03-06 02:02:15
- Request ID: `req_f1efcf15bcb7445d8a1e54db5003e69e`
- quality.stage: `S_COMPLETE`

## Node Order Check

| idx | node_id | status |
|---:|---|---|
| 1 | `core_reasoner_primary` | `OK` |
| 2 | `core_reasoner_secondary` | `OK` |
| 3 | `planner_constraints` | `OK` |
| 4 | `tool_planner` | `OK` |
| 5 | `safety_verifier` | `OK` |
| 6 | `consistency_verifier` | `OK` |
| 7 | `renderer_ja_c_style` | `OK` |
| 8 | `workbench_translate` | `OK` |
| 9 | `workbench_code_assist` | `OK` |

- Expected by ModelPlane ?3.1: renderer_ja_c_style is node #7.
- Actual: node #7 is `renderer_ja_c_style`.

## Retrieval Summary

- status: `OK`
- route_count: `6`
- candidate_count: `13`

| route_id | collection | status | candidate_count |
|---|---|---|---:|
| `route_bond` | `bond` | `OK` | 2 |
| `route_canon_style` | `canon_style` | `OK` | 1 |
| `route_canon_world` | `canon_world` | `OK` | 3 |
| `route_distilled` | `distilled` | `OK` | 2 |
| `route_episodic` | `episodic` | `OK` | 3 |
| `route_long_term` | `long_term` | `OK` | 2 |

## Model Control (9 rows)

| node_id | provider | model_id | status | reason_code |
|---|---|---|---|---|
| `core_reasoner_primary` | `gemini` | `gemini-2.5-pro` | `OK` | `None` |
| `core_reasoner_secondary` | `gemini` | `gemini-2.5-flash` | `OK` | `None` |
| `planner_constraints` | `gemini` | `gemini-2.5-pro` | `OK` | `None` |
| `tool_planner` | `gemini` | `gemini-2.5-flash` | `OK` | `None` |
| `safety_verifier` | `gemini` | `gemini-2.5-flash-lite` | `OK` | `None` |
| `consistency_verifier` | `gemini` | `gemini-2.5-flash-lite` | `OK` | `None` |
| `renderer_ja_c_style` | `gemini` | `gemini-2.5-flash` | `OK` | `None` |
| `workbench_translate` | `gemini` | `gemini-2.5-pro` | `OK` | `None` |
| `workbench_code_assist` | `gemini` | `gemini-2.5-pro` | `OK` | `None` |

## Retry Attempts

| attempt | request_id | quality.stage | all_nodes_ok |
|---:|---|---|---|
| 1 | `req_e627efde2bde462db5c4a86bae9d504b` | `S_ORCH_RUN` | `False` |
| 2 | `req_2f83b90d7e12492da9d4378790803ff8` | `S_ORCH_RUN` | `False` |
| 3 | `req_f1efcf15bcb7445d8a1e54db5003e69e` | `S_COMPLETE` | `True` |

## Final Narrative

`ユーザーより疑問符が送信されました。困惑、または支援を求めていると推測されます。`