import type { FastifyInstance, FastifyRequest } from "fastify";
import {
  latestHealth,
  healthHistory,
  listAnomalies,
  listIncidents,
  flowTimeline,
  diagnose,
} from "@compos/brain";

/**
 * Org scoping placeholder: the real API-key/entitlement middleware
 * (module referenced throughout the design doc, not part of this
 * consolidation) is expected to set `request.orgId` after validating a
 * key. Until that lands, this reads an `x-org-id` header directly —
 * fine for local development, NOT a substitute for authentication.
 * Do not deploy this route file to a public endpoint before the real
 * middleware is wired in front of it.
 */
function orgIdOf(req: FastifyRequest): string | undefined {
  const header = req.headers["x-org-id"];
  return typeof header === "string" ? header : undefined;
}

export default async function brainRoutes(app: FastifyInstance) {
  app.get("/v1/brain/health", async () => {
    const health = await latestHealth();
    if (!health) return { composite: null, subsystems: {}, openAnomalies: 0 };
    return health;
  });

  app.get("/v1/brain/health/history", async () => healthHistory(24 * 12)); // 24h @ 5min

  // Platform-team only — see queries.ts for why anomalies/incidents can't be org-scoped.
  app.get("/v1/brain/anomalies", async (req) => {
    const status = (req.query as { status?: string })?.status;
    return listAnomalies(status);
  });

  app.get("/v1/brain/incidents", async () => listIncidents());

  app.get("/v1/brain/flows/:flowId", async (req, reply) => {
    const { flowId } = req.params as { flowId: string };
    const result = await flowTimeline(flowId, orgIdOf(req));
    if (!result) return reply.code(404).send({ error: "flow not found" });
    return result;
  });

  app.post("/v1/brain/ask", async (req, reply) => {
    const body = req.body as { question?: string; flowId?: string; timeRangeHours?: number };
    if (!body?.question || body.question.length < 3) {
      return reply.code(400).send({ error: "question must be at least 3 characters" });
    }
    return diagnose({
      question: body.question,
      flowId: body.flowId,
      timeRangeHours: body.timeRangeHours ?? 24,
      orgId: orgIdOf(req),
    });
  });
}
