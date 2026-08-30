# 1. Append the RIA schema models to packages/db/prisma/schema.prisma (block above)
pnpm db:push                    # creates Jurisdiction, RegSourceCountry, CrawlTask, RegKnowledgeNode, RegLearningSignal
# 2. Load the multi-country registry
pnpm --filter @knowledge tsx src/ria/loadSources.ts
# 3. Rebuild so @knowledge/@review/@workers pick up the new code
pnpm install
# 4. Register the RIA Temporal schedules
pnpm --filter @workers tsx src/schedules-ria.ts
# 5. (workers already running) — riaCrawlWorkflow runs daily at 07:00