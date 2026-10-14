# Deploy the Internal Brain demo on Tencent Cloud Lighthouse

One small VM in Singapore serves the public demo over HTTPS. Everything in it is synthetic ("Company A"):
the Brain talks only to the in-stack mock Slack, Confluence, Jira and Drive. There is no setting for a real
workspace, and none must be added.

```
internet ──TCP 80/443──▶ caddy (TLS, headers, path allowlist)
                           └─▶ web :3100  (Next.js; browser calls same-origin /api/ib/* only)
                                 └─▶ ib :8100  (Internal Brain API, ledger in volume ib-var)
                                       └─▶ mock :8200 (synthetic platforms, signed webhooks back to ib)
```

Only Caddy publishes ports. `ib`, `mock` and `web` have no host ports, and `mock` sits on an internal-only
Docker network with no route to the internet. `ib` can reach the internet only for TokenHub.

| File | What it is |
|---|---|
| `deploy.sh` | Idempotent installer and operator: `deploy` (default), `reset`, `smoke`, `status`, `logs [svc]`, `down` |
| `upload.sh` | Run on your laptop: sends the committed source to the VM (`git archive`) and runs `deploy.sh` |
| `smoke.sh` | Checks the live URL: edge, sign-in, S1 with citations, S3 constant refusal, closed ports |
| `docker-compose.yml` | The four services, networks and volumes |
| `Caddyfile` | TLS for `$DOMAIN`, security headers, the only public routes |
| `Dockerfile.ib` | python:3.13-slim image for `ib` and `mock` (trimmed deps, import-checked at build) |
| `Dockerfile.web` | Node 20 multi-stage build to a Next.js standalone server |
| `requirements-ib.txt` | The 65 pinned packages the two Python services need (pins equal `databridge/requirements-lock.txt`) |
| `.env.example` | Every setting, with no real secrets |

## 1. Create the VM (Lighthouse console, about 5 minutes)

1. Open the Tencent Cloud console (international site) → **Lighthouse** → **Create**.
2. **Region:** Singapore.
3. **Image:** system image **Ubuntu 22.04 LTS** or **Ubuntu 24.04 LTS**. (The "Docker CE" app image also works;
   `deploy.sh` installs Docker when it is missing.)
4. **Plan:** 2 vCPU / 4 GB is comfortable. 2 vCPU / 2 GB works: `deploy.sh` adds a 2 GB swap file for the web
   build, or you can build the images on your laptop (section 6).
5. **Login method:** SSH key pair. Create one in the console (download the private key) or import your own
   public key (`~/.ssh/id_ed25519.pub`). Bind it to the instance.
6. Create the instance and note its **public IP**.
7. Instance → **Firewall**: keep only
   - TCP **22** (SSH; set the source to your own IP if you can),
   - TCP **80** (HTTP: Let's Encrypt validation, then a redirect to HTTPS),
   - TCP **443** (HTTPS).

   Delete every other rule (for example 3389 or 8080). Never open 8100, 8200 or 3100.
8. Check that you can log in: `ssh -i <your-key> ubuntu@<IP>`.

Costs: Lighthouse bundles are billed monthly; the smallest 2 vCPU plans cost a few US dollars a month, and new
accounts often get a free-trial or discounted first period. Check the price shown in the console when you buy.
TokenHub model calls are billed separately per token. Delete the instance after judging to stop all charges.

## 2. Deploy

The repo is **private**, so the VM cannot `git clone` it without a credential. The simplest path keeps all
credentials off the VM.

**Option A (recommended): upload from your laptop.** Commit `deploy/lighthouse/` first (only committed files
are sent). From the repo root, in Git Bash, macOS or Linux:

```bash
bash deploy/lighthouse/upload.sh ubuntu@<IP>
```

That copies the source to `/opt/walnut/repo` and runs `deploy.sh` there. Re-run it after every new commit.

**Option B: pipe `deploy.sh` and let the VM clone.** Works once the VM can read the repo: either the repo is
public (run the gitleaks history check first, docs/tencent/SECURITY.md §6), or the VM has a read-only deploy key:

```bash
ssh ubuntu@<IP> 'sudo ssh-keygen -t ed25519 -N "" -f /root/.ssh/id_ed25519 && sudo cat /root/.ssh/id_ed25519.pub'
# GitHub → IshaanSirbhaiya/Tencent → Settings → Deploy keys → Add (read-only) → paste the key
ssh ubuntu@<IP> 'sudo ssh -o StrictHostKeyChecking=accept-new -T git@github.com; true'
ssh ubuntu@<IP> 'sudo REPO_URL=git@github.com:IshaanSirbhaiya/Tencent.git bash -s' < deploy/lighthouse/deploy.sh
```

(With a public repo, the last line is just `ssh ubuntu@<IP> 'sudo bash -s' < deploy/lighthouse/deploy.sh`.)

**What `deploy.sh` does** (safe to re-run): installs Docker Engine, compose and buildx from Docker's apt repo
if missing; adds swap on small VMs; gets the source; writes `/opt/walnut/walnut.env` (mode 600) **only if it
does not exist**, with fresh keys from `openssl rand -hex 32` and a random demo passcode; runs
`docker compose up -d --build`; waits for health and for HTTPS; runs `smoke.sh`; prints the URL and the
passcode. The first run takes about 10 minutes (image builds, then the Brain indexes the whole world).

**Address.** Without a domain, the URL is `https://<ip-with-dashes>.sslip.io` (sslip.io resolves that name to
your IP, and Caddy gets a Let's Encrypt certificate for it). To use your own domain, create an A record pointing
at the IP, then run `deploy.sh` with `DOMAIN`:

```bash
ssh ubuntu@<IP> 'sudo DOMAIN=brain.example.com bash /opt/walnut/repo/deploy/lighthouse/deploy.sh'
```

## 3. Turn on Hunyuan (TokenHub)

Without a key every answer comes from the deterministic extractive answerer, and the demo still works. With a
key, the chain is `hy3` → `deepseek-v4.1-flash` → extractive, and only permitted, released passages are sent.

1. Get an API key from TokenHub (Tencent Cloud MaaS, international endpoint
   `https://tokenhub-intl.tencentcloudmaas.com/v1`).
2. Put it in the env file on the VM (not on a command line, where it would land in shell history):
   ```bash
   ssh ubuntu@<IP>
   sudo nano /opt/walnut/walnut.env        # set TOKENHUB_API_KEY=...
   sudo bash /opt/walnut/repo/deploy/lighthouse/deploy.sh
   ```
3. The smoke output line `model:` and each answer in the UI show which model answered.

To switch the model off again, set `IB_FORCE_OFFLINE=1` (or empty the key) and re-run `deploy.sh`.

## 4. Use the demo

Open the URL, enter the passcode and pick a persona (all synthetic): `jdoe` engineer, `priya.sre` SRE,
`ken.ext` contractor, `sara.sec` security, `leo.ops` ops, `maya.compliance` compliance officer, `admin.it`
simulator. The scenario script is in `INTERNAL_BRAIN.md` ("The five scenarios").

> **Known gap (web app, not this kit).** The demo API requires `IB_DEMO_PASSCODE` here, but the current sign-in
> page (`databridge/apps/internal_brain_web/app/login/page.tsx`, `lib/api.ts` `login`) does not send a passcode,
> so a UI sign-in gets 401 and shows "temporarily unavailable". `smoke.sh` signs in through the API with the
> passcode and passes. The web app needs a passcode field that sends `{"persona", "passcode"}` before the UI
> is usable on the public URL.

Show the passcode again: `ssh ubuntu@<IP> 'sudo grep IB_DEMO_PASSCODE /opt/walnut/walnut.env'`.
Rotate it: `ssh ubuntu@<IP> 'sudo IB_DEMO_PASSCODE=<new> bash /opt/walnut/repo/deploy/lighthouse/deploy.sh'`.

## 5. Reset, update, stop

| Task | Command (on the VM, or wrap in `ssh ubuntu@<IP> '...'`) |
|---|---|
| Reset the demo (fresh synthetic world, empty ledger, index and answer store; certificates kept) | `sudo bash /opt/walnut/repo/deploy/lighthouse/deploy.sh reset` |
| Run the smoke test again | `sudo bash /opt/walnut/repo/deploy/lighthouse/deploy.sh smoke` |
| Status / logs | `sudo bash /opt/walnut/repo/deploy/lighthouse/deploy.sh status` / `... logs ib` |
| Deploy new commits | `bash deploy/lighthouse/upload.sh ubuntu@<IP>` from your laptop (or re-pipe `deploy.sh` for Option B) |
| Stop (keeps data) | `sudo bash /opt/walnut/repo/deploy/lighthouse/deploy.sh down` |
| Stop billing | Lighthouse console → instance → **Terminate** |

The simulator's own "reset" (as `admin.it`) reloads only the mock world; `deploy.sh reset` also clears the ledger.

Smoke from your laptop (a real outside view, including the closed-port check):

```bash
BASE_URL=https://<ip-with-dashes>.sslip.io IB_DEMO_PASSCODE=<passcode> bash deploy/lighthouse/smoke.sh
```

## 6. Build the images on your laptop instead (small VM, or no build on the VM)

Lighthouse is x86_64 (on Apple Silicon, first `export DOCKER_DEFAULT_PLATFORM=linux/amd64`). From the repo root:

```bash
docker compose -p walnut --env-file deploy/lighthouse/.env.example -f deploy/lighthouse/docker-compose.yml build
docker save walnut-ib:latest walnut-web:latest | gzip | ssh ubuntu@<IP> 'gunzip | sudo docker load'
NO_BUILD=1 bash deploy/lighthouse/upload.sh ubuntu@<IP>
```

(`.env.example` only satisfies the compose file's required variables for the build; no secret goes into an
image. On a brand-new VM, run `deploy.sh` once first so Docker is installed, or install Docker before `docker load`.)

The web app alone, without Docker: `cd databridge/apps/internal_brain_web && npm ci && NEXT_OUTPUT=standalone npm run build`,
then `.next/standalone/server.js` with `.next/static` copied to `.next/standalone/.next/static`
(that is what `Dockerfile.web` does).

## 7. Try the whole kit locally first (optional)

With Docker Desktop, using ports that do not clash with a dev setup (do not use 80/443 if they are taken):

```bash
cp deploy/lighthouse/.env.example deploy/lighthouse/.env
# edit deploy/lighthouse/.env: DOMAIN=localhost, HTTP_PORT=18080, HTTPS_PORT=18443, and replace every
# replace-with-... value with `openssl rand -hex 32` (passcode: `openssl rand -hex 8`)
docker compose -p walnut-local --env-file deploy/lighthouse/.env -f deploy/lighthouse/docker-compose.yml up -d --build
BASE_URL=https://localhost:18443 SMOKE_INSECURE=1 COMPOSE_PROJECT=walnut-local \
  ENV_FILE=deploy/lighthouse/.env bash deploy/lighthouse/smoke.sh
docker compose -p walnut-local --env-file deploy/lighthouse/.env -f deploy/lighthouse/docker-compose.yml down -v
```

`DOMAIN=localhost` makes Caddy use its own local CA, hence `SMOKE_INSECURE=1`.

## 8. What the deployment enforces

| Control | Where |
|---|---|
| `IB_DEPLOYED=1` (refuses dev or short keys; `Secure` session cookie) and `IB_DEMO_LOGIN=1` are fixed, not settable from the env file | `docker-compose.yml` (`ib.environment`) |
| The stack refuses to start without `IB_DEMO_PASSCODE` and the keys | `${VAR:?}` in `docker-compose.yml`; checks in `deploy.sh` `ensure_env` |
| Keys generated per VM, never in git; env file mode 600 outside the checkout | `deploy.sh` `ensure_env` |
| Synthetic platforms only: `IB_MOCK_BASE=http://mock:8200`, no real platform variables passed | `docker-compose.yml` |
| Only 80/443 published; IB and mock unreachable from outside | `docker-compose.yml` (no `ports:` except caddy); `smoke.sh` checks 8100/8200/3100 and `docker ps` |
| Public paths: `/`, `/login`, `/audit`, `/sim`, `/_next/static/*`, `/api/ib/*`; everything else 404 | `Caddyfile`; `smoke.sh` probes IB and mock paths |
| The web proxy's own allowlist (no `/webhooks`) | `databridge/apps/internal_brain_web/app/api/ib/[...path]/route.ts` |
| HSTS, CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, `noindex`; no `Server` header | `Caddyfile` |
| Request body capped at 64 KB; header and body read timeouts | `Caddyfile` |
| Containers: non-root (`ib` uid 10001, `node`), read-only root filesystem, all capabilities dropped, `no-new-privileges` | `Dockerfile.ib`, `Dockerfile.web`, `docker-compose.yml` |

**Rate limiting is not built in.** Stock Caddy has no rate limiter (it is the third-party `caddy-ratelimit`
plugin, which needs a custom Caddy build), and the Brain has none either. Today the passcode is the gate on who
can ask questions, and model spend is bounded by your TokenHub quota. If the URL is shared widely, set a spend
limit in TokenHub, rotate the passcode, or `reset`/`down` the stack.

**Ledger anchoring.** The external head defaults to a file in the same volume, which is not off-host
(docs/tencent/SECURITY.md §3). `IB_ANCHOR_FILE` can point elsewhere inside the container; making it truly
off-host (a separately pushed repo) is not set up by this kit.

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| `deploy.sh` stops at "waiting for HTTPS" | Ports 80 and 443 open in the Lighthouse firewall? `deploy.sh logs caddy` shows the ACME error. With your own domain, the A record must already point at the IP. |
| Certificate errors with sslip.io | Let's Encrypt limits certificates per registered domain, and sslip.io is shared. Caddy falls back to ZeroSSL by itself; wait a few minutes and re-run `deploy.sh`. Using your own domain avoids it. |
| `ib` unhealthy or slow to start | It crawls and indexes the whole world before it answers `/health` (about a minute, longer on 2 vCPU). `deploy.sh logs ib`. |
| `refusing to start: set strong values for IB_TOKEN_KEY` | The env file has a short or dev key. Generate a new one with `openssl rand -hex 32`. |
| Build killed (out of memory) | 2 GB VM: make sure swap is on (`swapon --show`), or build on your laptop (section 6). |
| UI says "temporarily unavailable" at sign-in | The known passcode gap in section 4 (the API returns 401), or `ib` is down (`deploy.sh status`). |
| `503 {"error":"temporarily unavailable"}` on ask | The Brain failed closed (for example a ledger error). `deploy.sh logs ib`. |
| Audit verify reports problems after you edited the env file | `IB_LEDGER_KEY` changed after the ledger was written. Run `deploy.sh reset`. |
| `git pull failed` | Someone changed files in `/opt/walnut/repo`. Use `upload.sh`, which replaces the directory. |
| Need to look inside | `sudo docker compose -p walnut --env-file /opt/walnut/walnut.env -f /opt/walnut/repo/deploy/lighthouse/docker-compose.yml ps` |
