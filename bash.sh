# 1. Reconstruct the ENTIRE tree (all 16 batches + this wiring batch)
python3 split.py batch1.txt batch2.txt batch3.txt batch4.txt batch5.txt \
  batch6.txt batch7.txt batch8.txt batch9.txt batch10.txt \
  batch11.txt batch12.txt batch13.txt batch14.txt batch15.txt batch16.txt \
  batch17-ria-wiring.txt

# 2. Apply the one manual edit: add RIA_DRIFT to the ReviewQueueType enum
#    (packages/db/prisma/schema.prisma — shown in Part A)

# 3. Verify nothing is missing
find . -type f -not -path './node_modules/*' -not -path './.git/*' \
  -not -name 'batch*.txt' | wc -l          # expect ~270

# 4. Boot to prove it's deployable
docker compose up -d && pnpm install
pnpm db:push && pnpm db:push:brain && pnpm db:push:ethics
pnpm db:rls && pnpm db:vector
pnpm db:seed && pnpm db:seed:templates && pnpm db:seed:matrix
pnpm db:seed:sources && pnpm db:seed:prompts && pnpm db:seed:demo
pnpm test && pnpm audit:rls
pnpm dev

# 5. Push to GitHub
git init -b main
git add .
git commit -m "feat: AI-first compliance platform — 15 modules, Brain, RIA agent, ethics hotline (16-batch assembly)"
git remote add origin https://github.com/BatBond/Compliance.git
git push -u origin main            # PAT as password, or `gh auth login`
git tag -a v0.1.0-alpha -m "Assembled: see docs/ASSEMBLY.md"
git push origin v0.1.0-alpha