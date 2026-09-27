import type { FastifyInstance, FastifyRequest } from "fastify";
import { subscribeLive, type LiveEvent } from "@compos/brain";

/** Same placeholder org-scoping as routes/brain.ts — see the caveat there. */
function orgIdOf(req: FastifyRequest): string | undefined {
  const header = req.headers["x-org-id"];
  return typeof header === "string" ? header : undefined;
}

const HEARTBEAT_MS = 25_000;

export default async function brainStreamRoutes(app: FastifyInstance) {
  app.get("/v1/brain/stream", (req, reply) => {
    const orgId = orgIdOf(req);

    reply.hijack();
    reply.raw.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    });
    reply.raw.write(": connected\n\n");

    const unsubscribe = subscribeLive((evt: LiveEvent) => {
      // No org filter = platform-team view (sees everything); an org-scoped
      // caller only sees its own events. Platform-level events (no orgId)
      // are visible to everyone, same as the rest of the /v1/brain/* surface.
      if (orgId && evt.orgId && evt.orgId !== orgId) return;
      reply.raw.write(`data: ${JSON.stringify(evt)}\n\n`);
    });

    const heartbeat = setInterval(() => reply.raw.write(": ping\n\n"), HEARTBEAT_MS);

    req.raw.on("close", () => {
      clearInterval(heartbeat);
      unsubscribe();
      reply.raw.end();
    });
  });
}
