cd Compliance
python3 split.py batch12.txt          # add the new screens + routes

docker compose up -d                  # pgvector + postgres-ethics + redis + temporal
pnpm install
pnpm db:push && pnpm db:push:brain && pnpm db:push:ethics
pnpm db:rls && pnpm db:vector
pnpm db:seed && pnpm db:seed:templates && pnpm db:seed:matrix
pnpm db:seed:sources && pnpm db:seed:prompts
cp .env.example .env                  # SESSION_SECRET + one LLM key minimum
pnpm dev                              # api :3000 · web :3000 · brain :3010