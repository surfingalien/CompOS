# Kubernetes manifests

`namespace.yaml`, `brain.yaml`, and `api.yaml` deploy the two services
that actually exist in this repo. They follow one pattern (Deployment +
Service, `/healthz` probes, a `compos-env` Secret for configuration) —
copy it for `web`, `workers`, and `ethics` once those apps have real
Dockerfile-buildable code; there's no manifest for them here because
there's nothing yet to run.

Apply order:

```bash
kubectl apply -f namespace.yaml
kubectl -n compos create secret generic compos-env --from-env-file=../../.env
kubectl apply -f api.yaml -f brain.yaml
```

Managed Postgres and Redis (RDS/Cloud SQL + ElastiCache/Memorystore) are
recommended at this scale rather than running them in-cluster; point
`DATABASE_URL`/`REDIS_URL` in the `compos-env` secret at those instead.
