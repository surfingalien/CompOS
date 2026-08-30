curl localhost:3000/healthz                # {"ok":true,"service":"api"}
curl localhost:3010/status.json            # the brain is alive
curl -X POST localhost:3000/v1/auth/dev-login   # session cookie for the browser
# public DSAR intake end-to-end:
curl -X POST localhost:3000/v1/privacy/intake -H 'content-type: application/json' \
  -d '{"orgSlug":"acme","type":"ACCESS","email":"subject@example.com"}'