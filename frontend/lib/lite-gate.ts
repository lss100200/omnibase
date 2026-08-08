/**
 * P5.4C Lite product gate — pure invocation-allowance decision for the
 * workbench UI.
 *
 * The Invoke button and the Enter-key path must require EVERY condition at
 * the same time (fail closed, no single-condition shortcut):
 *
 * - `lite_gate_enabled`: the engineering-only AGENT_LITE_ENGINEERING_ENABLED
 *   flag is open;
 * - `engineering_assembled`: the tool-free Alpha composition is assembled in
 *   this environment (provider, environment, Phase 5 gates, migration head);
 * - `environment_allowed`: the process environment is an allowed engineering
 *   environment;
 * - `phase5_gates_all_false`: all three production Phase 5 Feature Gates
 *   remain exactly false;
 * - the UI context is complete: trimmed prompt text, a Workspace and an
 *   installed AgentVersion binding are selected.
 *
 * This helper is pure and host-independent so it can be unit-tested without a
 * browser; it never authorizes anything, it only labels whether the UI may
 * submit the engineering-only `no_tool` invocation.
 */

export interface LiteInvokePosture {
  readonly lite_gate_enabled: boolean
  readonly engineering_assembled: boolean
  readonly environment_allowed: boolean
  readonly phase5_gates_all_false: boolean
}

export function liteInvokeConditionsMet(
  posture: LiteInvokePosture | null | undefined,
): boolean {
  if (!posture) return false
  return Boolean(
    posture.lite_gate_enabled &&
      posture.engineering_assembled &&
      posture.environment_allowed &&
      posture.phase5_gates_all_false,
  )
}

export function canInvokeLiteAgent(
  posture: LiteInvokePosture | null | undefined,
  input: string,
  workspaceId: string,
  bindingId: string,
): boolean {
  return Boolean(
    liteInvokeConditionsMet(posture) &&
      input.trim().length > 0 &&
      workspaceId.length > 0 &&
      bindingId.length > 0,
  )
}
