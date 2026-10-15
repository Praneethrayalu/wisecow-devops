# Wisecow on Kubernetes — AccuKnox DevOps Trainee assessment

Candidate: **Repalle Praneeth Rayalu** · Praneeth1146a@gmail.com · github.com/Praneethrayalu

Wisecow is a tiny bash web server (`fortune | cowsay` over HTTP on port 4499). This repo containerises it,
deploys it to Kubernetes behind a TLS ingress, builds/pushes/deploys it with GitHub Actions, adds the
Problem-Statement-2 operations scripts, and (Problem Statement 3) a KubeArmor zero-trust runtime policy.

## Repository layout

| Path | What it is |
|---|---|
| `wisecow.sh` | the upstream app (unchanged apart from honouring `$SRVPORT`) |
| `Dockerfile` | Debian-slim image, non-root user, HEALTHCHECK |
| `k8s/namespace.yaml` `deployment.yaml` `service.yaml` `ingress.yaml` | Kubernetes manifests (2 replicas, probes, resource limits, hardened securityContext, ClusterIP + NodePort, TLS ingress) |
| `k8s/tls/cert-manager.yaml` | ClusterIssuer + Certificate that produce the `wisecow-tls` secret |
| `k8s/tls/make-selfsigned-secret.sh` | fallback: openssl self-signed secret when cert-manager is absent |
| `k8s/tls/letsencrypt-issuer.example.yaml` | production ACME issuer for a real domain |
| `.github/workflows/ci-cd.yaml` | lint → build & push to GHCR → deploy to the cluster (CD) |
| `scripts/app_health_checker.py` | PS2 objective 4 — HTTP up/down checker |
| `scripts/system_health_monitor.sh` | PS2 objective 1 — CPU / memory / disk / process monitor with alerts |
| `scripts/log_analyzer.py` | PS2 objective 3 (bonus) — Nginx/Apache access-log report |
| `kubearmor/` | PS3 — zero-trust KubeArmorPolicy + namespace default-deny posture |
| `docs/outputs/` | captured terminal output of every step below, run on a local kind cluster |

## 1. Build and run with Docker

```bash
docker build -t ghcr.io/praneethrayalu/wisecow:latest .
docker run -d --rm --name wisecow -p 4499:4499 ghcr.io/praneethrayalu/wisecow:latest
curl -s http://localhost:4499        # ASCII cow with a fortune
docker ps                             # STATUS shows (healthy) after ~30 s
```

## 2. Deploy to Kubernetes

Tested on kind v0.27 / Kubernetes v1.32, ingress-nginx v1.12.1, cert-manager v1.17.1.

```bash
# cluster prerequisites (once)
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.17.1/cert-manager.yaml
kubectl -n ingress-nginx wait --for=condition=ready pod -l app.kubernetes.io/component=controller --timeout=180s
kubectl -n cert-manager wait --for=condition=ready pod --all --timeout=180s

# wisecow
kind load docker-image ghcr.io/praneethrayalu/wisecow:latest --name wisecow   # kind only; a real cluster pulls from GHCR
kubectl apply -f k8s/namespace.yaml -f k8s/deployment.yaml -f k8s/service.yaml
kubectl apply -f k8s/tls/cert-manager.yaml -f k8s/ingress.yaml
kubectl -n wisecow rollout status deployment/wisecow
kubectl -n wisecow get all,certificate,ingress
```

Access:

```bash
kubectl -n wisecow port-forward svc/wisecow 8080:80 && curl http://localhost:8080          # plain, via the Service
# TLS via the ingress (hostname wisecow.local)
echo "127.0.0.1 wisecow.local" | sudo tee -a /etc/hosts
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8443:443
curl -vk https://wisecow.local:8443/     # TLSv1.3, CN=wisecow.local, HTTP/2 200
curl -I  http://wisecow.local:8080/      # 308 redirect to https (ssl-redirect annotation)
```

## 3. CI/CD (GitHub Actions)

`.github/workflows/ci-cd.yaml` runs on every push to `main`:

1. **lint-and-test** — ShellCheck on `wisecow.sh` and the scripts, `kubeconform` on the manifests, `py_compile` on the Python scripts.
2. **build-and-push** — Docker Buildx builds the image and pushes `ghcr.io/<owner>/wisecow:latest` and `:sha-<short>` to GitHub Container Registry using the automatic `GITHUB_TOKEN` (no extra secret needed). Pull requests only build + smoke-test.
3. **deploy** (Continuous Deployment, challenge goal) — decodes the `KUBE_CONFIG` repository secret (base64 kubeconfig of a service account scoped to the `wisecow` namespace), applies the manifests, pins the Deployment to the image **digest** just built, waits for the rollout and runs `scripts/app_health_checker.py` against the service.

Secrets to add in GitHub → Settings → Secrets and variables → Actions: `KUBE_CONFIG`. Make the GHCR package public (Packages → wisecow → Package settings) or add an `imagePullSecret`.

## 4. TLS (challenge goal)

The Ingress terminates TLS with the `wisecow-tls` secret. cert-manager issues it from a self-signed
ClusterIssuer here (no public DNS in a local cluster); swap `issuerRef` to `letsencrypt-prod`
(`k8s/tls/letsencrypt-issuer.example.yaml`) for a real domain. `make-selfsigned-secret.sh` creates the
same secret with openssl if cert-manager is not installed. HTTP is redirected to HTTPS (308).

## 5. Problem Statement 2 — scripts

```bash
python3 scripts/app_health_checker.py http://localhost:8080 https://wisecow.local:8443/ --insecure --retries 3
./scripts/system_health_monitor.sh -c 80 -m 80 -d 85 -p 400 -i 60      # loop every 60 s, alerts to console + system_health.log
python3 scripts/log_analyzer.py /var/log/nginx/access.log --top 10
```

## 6. Problem Statement 3 — KubeArmor zero-trust policy

`kubearmor/wisecow-zero-trust-policy.yaml` allow-lists only the binaries the app needs (bash, nc, fortune,
cowsay/perl, coreutils), makes `/etc`, `/usr/share`, `/proc` read-only, blocks `/etc/shadow`, `/tmp`,
`/root` and the service-account token directory, blocks `net_raw`/`sys_admin`, and the namespace
annotations in `namespace-default-posture.yaml` switch the default posture to **block** so anything not
listed is denied. Install KubeArmor with `karmor install`, then `kubectl apply -f kubearmor/`.

Honest note: on the sandbox used for this write-up the node kernel exposed no BPF-LSM/AppArmor, so KubeArmor
ran in visibility-only mode — the policy was accepted and applied (CRD validation passed) but enforcement was
not demonstrated there; on GKE/EKS/AKS or a kind node with AppArmor it enforces as written.

## Upstream problem statement

See the original README of https://github.com/nyrahul/wisecow (kept below as `UPSTREAM.md`).
