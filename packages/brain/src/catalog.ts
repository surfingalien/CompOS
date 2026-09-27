export const EVENT_CATALOG = {
  // ── shared with the module-10 external webhook taxonomy ──
  "control.test.failed": "ERROR",
  "control.test.passed": "INFO",
  "evidence.created": "INFO",
  "evidence.quality.changed": "INFO",
  "gap.detected": "WARN",
  "gap.remediated": "INFO",
  "policy.published": "INFO",
  "policy.acknowledgment.overdue": "WARN",
  "vendor.review.due": "WARN",
  "incident.created": "CRITICAL",
  "incident.notification.due": "CRITICAL",
  "regchange.detected": "INFO",
  "regchange.applied": "INFO",
  "questionnaire.drafted": "INFO",
  "privacy_request.created": "INFO",
  "trust.nda.signed": "INFO",

  // ── brain-native ──
  "connector.sync.completed": "INFO",
  "connector.sync.failed": "ERROR",
  "connector.silence.detected": "CRITICAL",
  "connector.paused": "WARN",
  "flow.started": "INFO",
  "flow.completed": "INFO",
  "flow.failed": "ERROR",
  "flow.stuck": "WARN",
  "llm.generation.completed": "INFO",
  "llm.generation.failed": "ERROR",
  "llm.cost.warning": "WARN",
  "sla.timer.fired": "INFO",
  "sla.timer.breached": "ERROR",
  "review.item.enqueued": "INFO",
  "review.item.overdue": "WARN",
  "api.request.failed": "WARN",
  "mcp.tool.denied": "WARN",
  "ethics.heartbeat": "INFO", // METADATA ONLY — see docs/architecture.md ethics isolation rule
  "ethics.sla.fired": "WARN", // METADATA ONLY
  "brain.anomaly.opened": "WARN",
  "brain.anomaly.resolved": "INFO",
  "brain.incident.opened": "CRITICAL",
  "brain.reflex.executed": "INFO",
  "brain.health.changed": "INFO",
} as const;

export type BrainEventType = keyof typeof EVENT_CATALOG;
export type BrainEventSeverity = (typeof EVENT_CATALOG)[BrainEventType];
