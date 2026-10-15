# Wisecow on Kubernetes - AccuKnox DevOps Trainee assessment

Candidate: **Repalle Praneeth Rayalu** · Praneeth1146a@gmail.com · B.Tech CSE (Cyber Security), LPU 2026

Upstream app: [nyrahul/wisecow](https://github.com/nyrahul/wisecow) (see `UPSTREAM.md`).

## 1. Problem statement and objectives

> Source: AccuKnox's published DevOps Trainee assessment. My own DevOps form needs a Google sign-in, so I
> worked from the same task text that appears in the QA Trainee form's PDF (PS1 + PS2) plus the optional
> PS3 (KubeArmor). If the DevOps form differs, the differences are not covered here.

- **PS1** - containerise the Wisecow app (Dockerfile), deploy it on Kubernetes (Deployment + Service),
  expose it, build and push the image with GitHub Actions, *challenge:* continuous deployment and TLS.
- **PS2** - any two of four ops scripts. I wrote three: system health monitor (Bash), application health
  checker (Python) and log analyzer (Python).
- **PS3 (optional)** - a zero-trust KubeArmor policy for the workload.

## 2. Architecture and design decisions

```
client --HTTPS--> ingress-nginx (TLS, HTTP->HTTPS 308) --> Service wisecow (ClusterIP :80)
       --> Deployment wisecow (2 pods, :4499) --> socat --fork--> wisecow.sh --handle --> fortune | cowsay
```

| Decision | Why |
|---|---|
| `socat ... fork` + a Bash handler per connection | The original `nc -l` loop accepted **one** connection, exited and restarted; requests arriving in that gap were reset (and 502s through the ingress). socat keeps listening and forks a short-lived handler per request, so back-to-back and parallel requests work. Stays a small shell app, no web framework. `max-children` caps parallel handlers (default 32). |
| Proper HTTP in the handler | Status line + `Content-Type` + exact `Content-Length` + `Connection: close`; 400 (malformed), 404 (path), 405 + `Allow` (method), 408 (silent client, 5 s), 413 (body > 1 MiB), 431 (> 100 headers), 500 (fortune/cowsay failed). Output is HTML-escaped. |
| Debian slim, pinned by digest | `fortune-mod`, `cowsay` and `socat` are packaged there; the digest makes builds start from the same bytes. |
| Non-root, read-only | Runs as uid 10001; app file is root-owned 0555; `readOnlyRootFilesystem: true` works because the new app writes nothing (the old one needed a FIFO). |
| HTTP probes | `startupProbe`, `readinessProbe`, `livenessProbe` all do a real `GET /` (which runs fortune + cowsay), not just a TCP connect. |
| ClusterIP + Ingress | ClusterIP is internal only; the Ingress is the single public entry point and terminates TLS. A NodePort (plain HTTP, bypasses TLS) is kept only as an optional debug file in `k8s/optional/`. |
| cert-manager | Self-signed `ClusterIssuer` for local testing; `k8s/tls/letsencrypt-issuer.example.yaml` for a real domain. A self-signed certificate is **not** publicly trusted - clients must be given its CA (tests prove the public trust store rejects it). |

## 3. Project structure

```
wisecow.sh                      the app (listener + per-connection HTTP handler)
Dockerfile, .dockerignore, .hadolint.yaml
k8s/  namespace, deployment, service, ingress, kustomization
k8s/tls/  cert-manager issuer + certificate, Let's Encrypt example, openssl fallback script
k8s/optional/nodeport-service.yaml   debug only, not deployed by kustomize/CI
k8s/schemas/kubearmorpolicy_v1.json  JSON schema generated from the KubeArmor CRD (for kubeconform)
kubearmor/  zero-trust allow-list policy + namespace "block" posture (PS3)
scripts/  system_health_monitor.sh, app_health_checker.py, log_analyzer.py (PS2)
tests/  smoke_test.sh (black-box HTTP), test_scripts.py (pytest for PS2 scripts)
.github/workflows/ci-cd.yaml
docs/evidence/   outputs of the verification runs (current code)
docs/baseline/   outputs from the first version, kept for comparison
```

## 4. Prerequisites

Docker 24+, kubectl 1.29+, a cluster (kind/minikube/real) with ingress-nginx and cert-manager, Python 3.9+
(standard library only for the scripts; `pytest` for the tests), curl. Tested with kind v0.27 /
Kubernetes v1.32, ingress-nginx, cert-manager, Python 3.11.

## 5. Install and run

```bash
# local container
docker build -t wisecow:local .
docker run -d --name wisecow --read-only --cap-drop ALL --security-opt no-new-privileges -p 4499:4499 wisecow:local
curl -i http://localhost:4499/
tests/smoke_test.sh http://localhost:4499

# Kubernetes (kind example)
kind create cluster --name wisecow
kind load docker-image wisecow:local --name wisecow
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.2/cert-manager.yaml
kubectl apply -k k8s
kubectl -n wisecow set image deployment/wisecow wisecow=wisecow:local
kubectl apply -f k8s/tls/cert-manager.yaml           # or: k8s/tls/make-selfsigned-secret.sh
kubectl -n wisecow rollout status deployment/wisecow
echo "127.0.0.1 wisecow.local" | sudo tee -a /etc/hosts
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8443:443 8080:80 &
kubectl -n wisecow get secret wisecow-tls -o jsonpath='{.data.ca\.crt}' | base64 -d > wisecow-ca.crt
curl --cacert wisecow-ca.crt https://wisecow.local:8443/
```

Rollback: `kubectl -n wisecow rollout undo deployment/wisecow` (3 old ReplicaSets are kept; the CI deploy
does this automatically if a rollout does not finish in 180 s).

PS2 scripts:

```bash
bash scripts/system_health_monitor.sh -c 80 -m 80 -d 85 -p 400        # -j for JSON, -i 60 to repeat
python3 scripts/app_health_checker.py https://wisecow.local:8443/ --cacert wisecow-ca.crt --retries 3
python3 scripts/log_analyzer.py docs/sample_access.log --top 5          # --json for JSON
```
Exit codes: 0 = healthy / report produced, 1 = alert / DOWN / nothing parseable, 2 = bad arguments or
unreadable file.

## 6. Environment variables and secrets

| Name | Where | Meaning |
|---|---|---|
| `SRVPORT` | container | listen port (default 4499) |
| `MAX_CONN` | container | max parallel request handlers (default 32) |
| `READ_TIMEOUT` | container | seconds to wait for a request before 408 (default 5) |
| `GITHUB_TOKEN` | Actions (automatic) | pushes to GHCR; only the `push` job gets `packages: write` |
| `KUBE_CONFIG` | Actions **secret** | base64 kubeconfig for a service account limited to the `wisecow` namespace, e.g. `base64 -w0 kubeconfig` |
| `DEPLOY_ENABLED` | Actions **variable** | set to `true` to enable the deploy job; otherwise it shows as *skipped* |

No secret is committed to this repository.

## 7. Tests and actual results

| Check | Result (15 Oct 2026, this repo's final code) | Evidence |
|---|---|---|
| shellcheck, py_compile, hadolint, actionlint, bandit | no findings | `docs/evidence/01_static_checks.txt` |
| kubeconform (app, TLS, optional, KubeArmor) | 4/4 + 6/6 valid | same file |
| `kubectl apply --dry-run=server` (all manifests incl. CRDs) | accepted | `02_server_side_dry_run.txt` |
| image build; container non-root, read-only, healthy | OK, 142 MB | `03_docker_build.txt`, `04_container_smoke_test.txt` |
| `tests/smoke_test.sh` on the new image | 8/8 checks passed | `04_container_smoke_test.txt` |
| same smoke test on the **original** image | 6 checks failed (resets, no 405/404) | `05_baseline_old_image_smoke_test.txt` |
| `pytest tests/test_scripts.py` | 40 passed | `06_ops_script_tests.txt` |
| rollout on kind, 2/2 ready, certificate Ready | OK | `07_kind_rollout.txt` |
| HTTPS verified with CA, public trust store rejects, 308 redirect, TLS 1.1 refused, 100-request burst through ingress | 100/100 HTTP 200 | `08_ingress_tls_burst.txt` |
| scripts: success + failure cases | exit codes 0/1/2 as documented | `09_ops_scripts_demo.txt` |

The GitHub Actions workflow itself has **not** run on GitHub yet (the repo is not pushed); actionlint passes
and each job's commands were run locally (see the evidence files).

## 8. CI/CD (GitHub Actions)

`lint` (shellcheck, py_compile, script unit tests, kubeconform for every manifest) → `image-test` (build,
run hardened, wait for healthy, smoke test) → `push` (main only; pushes **the tested image** as
`sha-<commit>` and `latest`, records the digest) → `deploy` (only when `DEPLOY_ENABLED=true`; checks
`KUBE_CONFIG`, `kubectl apply -k k8s`, `set image ...@sha256:<digest>`, waits for rollout, rolls back on
failure, verifies with the health checker and the smoke test). Pull requests run the first two jobs only.
Default token permission is `contents: read`.

## 9. Known limitations

- The DevOps form's own problem statement was not readable (Google sign-in); see section 1.
- The workflow has not been executed on GitHub; deployment has been verified only on a local kind cluster,
  not on a cloud cluster with a public domain.
- KubeArmor policy: syntax validated (schema + server dry run) and applied, but **enforcement is unverified**:
  the test machine's kernel had no BPF-LSM/AppArmor, so KubeArmor ran in visibility-only mode.
- Each request forks bash + fortune + perl (cowsay): fine for this app, not for high traffic.
- Base image digest and apt packages need periodic refresh for security fixes (DL3008 ignored, reason in `.hadolint.yaml`).

## 10. Security considerations

Non-root uid 10001, `runAsNonRoot`, `allowPrivilegeEscalation: false`, all capabilities dropped,
seccomp `RuntimeDefault`, read-only root filesystem, no service-account token, resource limits, TLS at
the ingress with forced redirect and HSTS, least-privilege workflow token, kubeconfig only from a secret
via env (never echoed or inlined), deploys by immutable digest, input limits in the handler (header count,
body size, read timeout, concurrency cap), HTML-escaped output.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `ImagePullBackOff` on kind | `kind load docker-image <image> --name wisecow`, then `kubectl set image` to that tag |
| `curl: (60)` over HTTPS | expected with a self-signed cert: pass `--cacert wisecow-ca.crt` (do not use `-k` in automation) |
| Ingress 404 | `wisecow.local` must resolve to the ingress (`/etc/hosts`) and the Host header must match |
| Certificate not Ready | `kubectl describe certificate -n wisecow wisecow-tls`; is cert-manager running? |
| Deploy job skipped | set the repository variable `DEPLOY_ENABLED=true` and the `KUBE_CONFIG` secret |
| `docker build` fails on `COPY --chmod` | not used any more; plain `docker build` works without BuildKit |

## 12. Evidence of verification

All outputs are in `docs/evidence/` (each file starts with the exact command and ends with its exit code).
`docs/baseline/` holds the outputs of the first version for comparison. `INTERVIEW_PREP.md` explains the
design in interview form.
