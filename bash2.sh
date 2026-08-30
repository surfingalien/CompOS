cd Compliance
# Reconstruct EVERYTHING (newest write wins on overrides).
# batch1–10 = the knowledge-base batches; batch11–19 = the batches I emitted after.
python3 split.py batch1.txt batch2.txt batch3.txt batch4.txt batch5.txt \
  batch6.txt batch7.txt batch8.txt batch9.txt batch10.txt \
  batch11.txt batch12.txt batch13.txt batch14.txt batch15.txt \
  batch16.txt batch17.txt batch18.txt batch19.txt

# Sanity: expect ~270 files
find . -type f -not -path './node_modules/*' -not -path './.git/*' -not -name 'batch*.txt' | wc -l

# One manual edit: add RIA_DRIFT to ReviewQueueType enum, then:
pnpm install
docker compose up -d
pnpm db:push && pnpm db:push:brain && pnpm db:push:ethics
pnpm db:rls && pnpm db:vector
pnpm db:seed && pnpm db:seed:templates && pnpm db:seed:matrix
pnpm db:seed:sources && pnpm db:seed:prompts
pnpm test && pnpm audit:rls

# Push to GitHub
git init -b main && git add . && git commit -m "feat: AI-first compliance platform — full 19-batch assembly"
git remote add origin https://github.com/BatBond/Compliance.git
git push -u origin main
git tag -a v0.1.0-alpha -m "Assembled: see docs/ASSEMBLY.md" && git push origin v0.1.0-alpha