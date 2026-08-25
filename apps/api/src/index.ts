import Fastify from "fastify";
import brainRoutes from "./routes/brain.js";

const app = Fastify({ logger: true });

app.get("/healthz", async () => ({ ok: true }));
await app.register(brainRoutes);

const port = Number(process.env.API_HTTP_PORT ?? 3000);
app.listen({ port, host: "0.0.0.0" }).catch((err) => {
  app.log.error(err);
  process.exit(1);
});
