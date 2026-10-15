# Interview prep - DevOps Trainee (Wisecow)

## The solution in one minute
Wisecow prints a random quote (`fortune`) drawn by a cow (`cowsay`) over HTTP. I packaged it in a small
non-root Docker image, run 2 replicas on Kubernetes behind a ClusterIP Service and an nginx Ingress that
terminates TLS (certificate from cert-manager), and built a GitHub Actions pipeline that lints, builds and
tests the image, pushes the tested image to GHCR, and (when enabled) deploys it by digest, waits for the
rollout and checks that the app answers. I also wrote three ops scripts and a KubeArmor policy.

## Request flow
1. Browser → `https://wisecow.local` → ingress-nginx (TLS handshake, HTTP is redirected with 308).
2. Ingress → Service `wisecow` (ClusterIP, port 80) → one of the pods on port 4499.
3. In the pod, `socat` accepts the TCP connection and forks `wisecow.sh --handle`.
4. The handler reads the request line and headers, validates them, runs `fortune | cowsay`, HTML-escapes
   the text and writes `HTTP/1.1 200 OK` + headers + `<pre>...</pre>`, then exits.

## Key design choices and trade-offs
- **socat fork vs Python http.server vs nginx+CGI**: socat keeps the original shell app and fixes the real
  bug (listener gaps) with one extra tiny package. Trade-off: one process tree per request, so it is not
  for heavy traffic; `max-children` caps it.
- **Debian slim vs Alpine**: fortune/cowsay are straightforward on Debian; Alpine would be smaller but
  needs different packages and paths. Image is 142 MB.
- **HTTP probes vs TCP probes**: TCP only proves the port is open; HTTP proves fortune+cowsay work.
- **Opt-in deploy**: without a cluster the deploy job is *skipped* (grey), never a fake green.
- **Push the tested image**: the image built and tested in `image-test` is saved and pushed as-is, so
  what is deployed is exactly what passed the tests.

## Top 3 defects found and fixed
1. **Connection resets / 502s** (High): the original `nc -l` loop served one connection at a time and
   restarted between requests. 20 back-to-back requests: 10 failed; burst through the ingress: 8-11 of 20
   were 502. Fixed with socat fork-per-connection: 100/100 through the ingress now succeed.
2. **Invalid HTTP** (Medium): the old response was `HTTP/1.1 200` with no reason phrase, no Content-Type,
   no Content-Length, and every method/path got the same answer. Now: full headers, 404/405/400/408/413/431.
3. **CI that could not run** (High, found in the first version): invalid YAML in a step name, an upper-case
   GHCR image name, a shellcheck failure, a deploy step that inlined the kubeconfig secret into the script
   and failed with an unclear error when no cluster existed. Fixed and checked with actionlint.
Also: a bug in my own fix was caught by a unit test - in Bash 5.2 `&` in a `${var//x/y}` replacement means
"the matched text", so HTML escaping produced `<lt;`. Quoting the replacement fixed it.

## Limitations that remain
Workflow not yet run on GitHub; no cloud deployment or public domain (so no Let's Encrypt certificate);
KubeArmor enforcement not proven (kernel without BPF-LSM/AppArmor); DevOps form text unverified.

## 10 likely questions about my code

1. **Why did the original app drop requests?** `nc -l` handles one connection and exits; the `while` loop
   then starts a new `nc`. Requests arriving in between are refused/reset. Behind the ingress, nginx sees the
   reset as a bad upstream and returns 502.
2. **How does your fix work?** `socat TCP-LISTEN:4499,fork,max-children=32 EXEC:"wisecow.sh --handle"`.
   The listening socket never closes; each accepted connection gets its own child process.
3. **What happens with a POST or an unknown path?** POST → 405 with `Allow: GET, HEAD` (its body is read and
   discarded first so the client isn't reset); unknown path → 404; malformed request line → 400.
4. **Why `readOnlyRootFilesystem: true` now and not before?** The old script created a FIFO file in its
   working directory. The new one writes nothing, so the root filesystem can be read-only; a 1 MiB in-memory
   `/tmp` is mounted just in case.
5. **Difference between ClusterIP, NodePort and Ingress here?** ClusterIP gives the pods a stable internal
   IP; NodePort opens a port on every node (plain HTTP, bypasses TLS) - only an optional debug file; the
   Ingress is the one public entry and does TLS and host routing.
6. **Is your certificate trusted?** Only by clients given its CA. It's self-signed by cert-manager for local
   testing; my test shows the public trust store rejects it. For production I'd use the Let's Encrypt
   ClusterIssuer with a real DNS name.
7. **Why deploy by digest instead of `:latest`?** A tag can be moved; a digest identifies exact bytes, so the
   rollout is reproducible and `rollout undo` goes back to a known image.
8. **How do you stop a bad release?** `maxUnavailable: 0` keeps old pods serving until new ones are ready;
   readiness uses a real HTTP check; CI waits 180 s for the rollout and runs `kubectl rollout undo` if it fails.
9. **What permissions does the workflow have?** `contents: read` by default; only the push job has
   `packages: write`. The kubeconfig is read from a secret through an environment variable, never printed.
10. **What does your KubeArmor policy do and is it enforced?** It's an allow-list of the binaries (bash, socat,
   fortune, cowsay, perl, date, realpath), read-only file paths and TCP networking the app needs, plus a
   namespace posture of `block` for everything else. Validated and applied, but enforcement is unverified
   because the test kernel had no BPF-LSM/AppArmor - I'd test it on a node that has one, e.g. by trying
   `kubectl exec ... -- apt-get` and checking it's blocked.

Bonus: **Exit codes of your scripts?** 0 healthy, 1 alert/DOWN, 2 bad arguments or unreadable input, so
they work in cron, CI and Kubernetes jobs. `--json`/`-j` print only JSON on stdout; logs go to stderr.
