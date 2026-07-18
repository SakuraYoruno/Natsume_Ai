import assert from 'node:assert/strict'
import test from 'node:test'

import { endpointPost } from './endpointClient.js'
import { ENDPOINT_REGISTRY } from './endpointRegistry.js'
import {
  configureOperatorCommandGateway,
  mergeCommandPayload,
  resetOperatorCommandGateway,
} from './operatorCommandGateway.js'
import { isOperatorCommandId } from './generated/operatorCommandClient.ts'
import { cancelTaskGraph } from '../../features/execution/taskRuntimeApi.js'

test('configured transport still uses the same-origin BFF without browser signing', async () => {
  const observed = []
  configureOperatorCommandGateway({
    fetchImpl: async (url, init) => {
      observed.push({ url, init })
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            allowed: true,
            audit_ref: 'audit://operator/avatar/1',
            claim_ref: 'claim://operator/avatar/1',
            command_id: 'avatar.behavior.apply',
            duplicate: false,
            executed: true,
            reason_code: 'OBS_COMMAND_COMPLETED',
            result_ref: 'obs-command://result/avatar/1',
            status: 'completed',
            terminal_state: 'SUCCEEDED',
          }
        },
      }
    },
  })

  try {
    const receipt = await endpointPost(
      ENDPOINT_REGISTRY.digitalLife.avatarBehavior,
      { behavior_id: 'wave', emotion_tag: 'happy' },
      {},
      {},
      {
        correlationId: 'corr.avatar.1',
        idempotencyKey: 'idem.avatar.1',
        nonce: 'nonce.avatar.1',
      },
    )
    assert.equal(receipt.status, 'completed')
    assert.equal(observed[0].url, '/v1/host/operator-command-bff/avatar.behavior.apply')
    assert.deepEqual(JSON.parse(observed[0].init.body), {
      payload: { behavior_id: 'wave', emotion_tag: 'happy' },
    })
    assert.equal(observed[0].init.headers['X-Natsume-Idempotency-Key'], 'idem.avatar.1')
    assert.equal(observed[0].init.headers['X-Natsume-Proof'], undefined)
    assert.equal(ENDPOINT_REGISTRY.digitalLife.avatarBehavior.path, undefined)
  } finally {
    resetOperatorCommandGateway()
  }
})

test('production command path uses same-origin BFF without an injected signer', async () => {
  const previousFetch = globalThis.fetch
  const observed = []
  globalThis.fetch = async (url, init) => {
    observed.push({ url, init })
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          allowed: true,
          audit_ref: 'audit://operator/bff/1',
          claim_ref: 'claim://operator/bff/1',
          command_id: 'avatar.behavior.apply',
          duplicate: false,
          executed: true,
          reason_code: 'OBS_COMMAND_COMPLETED',
          result_ref: 'result://operator/bff/1',
          status: 'completed',
        }
      },
    }
  }
  resetOperatorCommandGateway()

  try {
    const receipt = await endpointPost(
      ENDPOINT_REGISTRY.digitalLife.avatarBehavior,
      { behavior_id: 'wave' },
      {},
      {},
      {
        correlationId: 'corr.bff.1',
        idempotencyKey: 'idem.bff.1',
        nonce: 'nonce.bff.1',
      },
    )
    assert.equal(receipt.status, 'completed')
    assert.equal(observed[0].url, '/v1/host/operator-command-bff/avatar.behavior.apply')
    assert.equal(observed[0].init.credentials, 'same-origin')
    assert.equal(observed[0].init.headers['X-Natsume-Proof'], undefined)
    assert.equal(observed[0].init.headers['X-Natsume-Identity'], undefined)
    assert.deepEqual(JSON.parse(observed[0].init.body), {
      payload: { behavior_id: 'wave' },
    })
  } finally {
    globalThis.fetch = previousFetch
  }
})

test('TaskGraph cancellation uses the generated high-risk Operator command', async () => {
  const observed = []
  configureOperatorCommandGateway({
    fetchImpl: async (url, init) => {
      observed.push({ url, init })
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            allowed: true,
            audit_ref: 'secure-ingress-audit://task-graph-cancel',
            claim_ref: 'secure-ingress-claim://task-graph-cancel',
            command_id: 'task_graph.cancel',
            duplicate: false,
            executed: true,
            reason_code: 'OBS_COMMAND_COMPLETED',
            result_ref: 'obs-command://result/task-graph/cancel/1',
            status: 'completed',
            terminal_state: 'SUCCEEDED',
          }
        },
      }
    },
  })
  try {
    await cancelTaskGraph('graph-ui-cancel', 4, 'operator_requested')
    assert.equal(observed[0].url, '/v1/host/operator-command-bff/task_graph.cancel')
    assert.deepEqual(JSON.parse(observed[0].init.body), {
      payload: {
        graph_id: 'graph-ui-cancel',
        graph_revision: 4,
        reason_code: 'OPERATOR_REQUESTED',
      },
    })
    assert.equal(ENDPOINT_REGISTRY.execution.taskGraphCancel.risk, 'high')
  } finally {
    resetOperatorCommandGateway()
  }
})

test('path and query authority are normalized into payload and caller authority conflicts fail closed', () => {
  assert.deepEqual(
    mergeCommandPayload(
      { reason: 'reviewed' },
      { candidateId: 'candidate-1' },
      { run_id: 'run-1' },
    ),
    { candidate_id: 'candidate-1', reason: 'reviewed', run_id: 'run-1' },
  )
  assert.throws(
    () => mergeCommandPayload({ candidate_id: 'one' }, { candidateId: 'two' }),
    /OPERATOR_COMMAND_PAYLOAD_CONFLICT/,
  )
})

test('all server-bound Monitor mutations no longer retain a domain writer URL', () => {
  const entries = [
    ENDPOINT_REGISTRY.observability.codexBioOpsAdoptionAccept,
    ENDPOINT_REGISTRY.observability.codexBioOpsAdoptionReject,
    ENDPOINT_REGISTRY.observability.codexBioOpsDailyCycleRun,
    ENDPOINT_REGISTRY.observability.codexBioOpsOwnerInboxMaterialize,
    ENDPOINT_REGISTRY.observability.codexBioOpsOwnerIntakePackets,
    ENDPOINT_REGISTRY.observability.codexBioOpsOwnerQueuesConsume,
    ENDPOINT_REGISTRY.observability.codexBioOpsOwnerStateAccept,
    ENDPOINT_REGISTRY.observability.codexBioOpsOwnerStateImport,
    ENDPOINT_REGISTRY.observability.codexBioOpsOwnerStateReject,
    ENDPOINT_REGISTRY.digitalLife.avatarBehavior,
    ENDPOINT_REGISTRY.digitalLife.sysModeAutoRules,
    ENDPOINT_REGISTRY.digitalLife.sysModeEvent,
    ENDPOINT_REGISTRY.operations.forgeProposalApprove,
    ENDPOINT_REGISTRY.operations.forgeProposalRollback,
  ]
  for (const entry of entries) {
    assert.equal(entry.path, undefined)
    assert.match(entry.commandId, /^[a-z][a-z0-9_.]+$/)
  }
})

test('registry contract has no medium/high direct writer and every command is generated', () => {
  const visit = (value, keyPath = []) => {
    if (!value || typeof value !== 'object') return
    if ('risk' in value) {
      if (value.commandId) {
        assert.equal(value.path, undefined, `${keyPath.join('.')} retained writer path`)
        assert.equal(isOperatorCommandId(value.commandId), true, `${value.commandId} is not generated`)
      }
      if (value.risk === 'medium' || value.risk === 'high') {
        assert.ok(
          value.commandId || value.queryOnly === true,
          `${keyPath.join('.')} is an unclassified non-query endpoint`,
        )
      }
      return
    }
    Object.entries(value).forEach(([key, child]) => visit(child, [...keyPath, key]))
  }
  visit(ENDPOINT_REGISTRY)
})

test('interactive and retired Pentest mutations are absent from the Operator UI registry', () => {
  assert.equal(ENDPOINT_REGISTRY.execution.agentRun, undefined)
  assert.equal(ENDPOINT_REGISTRY.execution.chatDebug, undefined)
  assert.equal(ENDPOINT_REGISTRY.pentest.cancel, undefined)
  assert.equal(ENDPOINT_REGISTRY.pentest.approveSideEffect, undefined)
  assert.equal(ENDPOINT_REGISTRY.pentest.continueSideEffect, undefined)
})

test('shared endpoint client rejects every unclassified non-GET request', async () => {
  await assert.rejects(
    async () => endpointPost({ service: 'obs', path: '/domain/writer', risk: 'low' }, {}),
    /DIRECT_MUTATION_ENDPOINT_DENIED/,
  )
})
