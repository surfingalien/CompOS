# assemble ALL batches (newest wins on overrides)
python3 split.py batch1.txt batch2.txt batch3.txt batch4.txt batch5.txt \
  batch6.txt batch7.txt batch8.txt batch9.txt batch10.txt \
  batch11.txt batch12.txt batch13.txt batch14.txt batch15.txt \
  batch16.txt batch17.txt batch18.txt batch19.txt batch20.txt \
  batch21.txt batch22.txt

# boot
cp .env.example .env
pnpm install
docker compose up -d
pnpm db:push && pnpm db:push:brain && pnpm db:push:ethics
pnpm db:rls && pnpm db:vector
pnpm db:seed && pnpm db:seed:templates && pnpm db:seed:matrix
pnpm db:seed:sources && pnpm db:seed:prompts && pnpm db:seed:demo
pnpm dev