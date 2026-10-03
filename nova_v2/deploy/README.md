# Running NOVA on your own server

Everything NOVA needs runs in this folder, on one machine, reachable only over your Tailscale network:

| Service | What it is |
|---|---|
| `qwen` | Qwen 3.8 27B on one GPU, with an OpenAI-compatible API |
| `db` | Postgres 17 with pgvector. Holds all NOVA data, in `data/postgres` |
| `auth` | Sign-up and sign-in (Supabase Auth) |
| `rest` | The database's HTTP API, used by the NOVA server |
| `searxng` | Private web search for NOVA's `web_search` tool |
| `nova` | The NOVA API that the Android app talks to |
| `gateway` | Caddy. It routes requests and checks the model key |

The address is `https://<host>.<your-tailnet>.ts.net`, published by `tailscale serve`. It works only from devices on your tailnet (or ones you have shared the machine with) - it is **not** exposed to the internet. The real address is in `.env` as `PUBLIC_URL`; this file keeps it out of the repo, which is public.

## Before you start

The machine needs:

- Linux with an NVIDIA GPU that has enough memory for Qwen 27B (the image is built for an RTX 3090, 24 GB)
- Docker with the compose plugin, and the NVIDIA Container Toolkit (`docker run --rm --gpus all ubuntu nvidia-smi` should list your GPUs)
- Tailscale, signed in, with **MagicDNS** and **HTTPS certificates** turned on in the admin console (DNS page)
- `ufw`, `openssl`, `python3` and `git`

## First install

**1. Get the code and do the root setup.** This adds you to the docker group, lets you run `tailscale serve`, opens the firewall to the tailnet only, and creates `/srv/nova`:

```sh
git clone <this repo> ~/Project-Nova
sudo bash ~/Project-Nova/nova_v2/deploy/setup-root.sh
```

Log out and back in so the new groups take effect.

**2. Copy this folder and the server code into `/srv/nova`.** `compose.yml` builds the API from `/srv/nova/server` and reads the schema from `/srv/nova/server/db`, so both have to be there:

```sh
cp -r ~/Project-Nova/nova_v2/deploy/. /srv/nova/
cp -r ~/Project-Nova/nova_v2/server /srv/nova/server
cd /srv/nova
```

**3. Generate the secrets:**

```sh
bash gen-keys.sh
```

This writes `.env` and only fills in what's missing, so re-running it never changes a key in use. Then check two things in `.env`:

- `PUBLIC_URL` must be the machine's real tailnet name - compare it with `tailscale status`. If Tailscale wasn't up it will say `.local`; fix it by hand.
- `QWEN_GPU` defaults to `1` (the second GPU). On a single-GPU machine set it to `0`. `nvidia-smi -L` lists them.

**4. Start everything:**

```sh
bash install.sh
```

It applies the schema, starts the stack and publishes it on the tailnet. Qwen takes a few minutes to load on first start (the first start also downloads the weights, tens of GB): `docker compose logs -f qwen`.

**5. Check it.** From any device on the tailnet, `https://<host>.<your-tailnet>.ts.net/health` should return `{"status":"ok"}`.

## The Android app

The app is built with the server address baked in, and neither the address nor the client key is in the repo. Each developer adds them once to `nova_v2/app/local.properties`:

```
NOVA_BASE_URL=https://<host>.<your-tailnet>.ts.net
NOVA_API_KEY=<the NOVA_API_KEY from .env>
NOVA_SERVER_IP=100.x.y.z
```

To produce exactly those three lines on the server, without handing out the rest of `.env`:

```sh
cd /srv/nova && { echo "NOVA_BASE_URL=$(grep '^PUBLIC_URL=' .env | cut -d= -f2-)"; grep '^NOVA_API_KEY=' .env; echo "NOVA_SERVER_IP=$(tailscale ip -4)"; }
```

`NOVA_SERVER_IP` is the machine's Tailscale address. The app uses it when the name can't be looked up - the Android emulator can't use MagicDNS, and a device the machine is only shared with may not resolve it either. HTTPS is still checked against the name, so it stays secure.

Rebuild the app after changing `local.properties`: the values are compiled in. A 403 or "Check NOVA_API_KEY" means the key doesn't match the server's.

The phone needs the Tailscale app, signed in and connected. Each person makes their own NOVA account in the app; accounts are confirmed on sign-up.

> Don't send `.env` itself around. `POSTGRES_PASSWORD`, `JWT_SECRET` and `SERVICE_ROLE_KEY` give full access to every user's data and let whoever holds them sign in as anyone.

## Using the model from your own code

It speaks the OpenAI chat-completions protocol:

```python
from openai import OpenAI
client = OpenAI(base_url="https://<host>.<your-tailnet>.ts.net/v1",
                api_key="<LLM_SHARE_KEY from .env>")
client.chat.completions.create(model="qwen3.8-27b", messages=[{"role": "user", "content": "hi"}])
```

Tool calling works; send tools in OpenAI format. Thinking is on by default and comes back separately, in `reasoning_content`. To turn it off for one request, add `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`.

## Giving your team access

**Use the app.** In the Tailscale admin console, either invite them to your tailnet (Users -> Invite) or share just this machine with them (Machines -> this machine -> Share). Sharing is the smaller grant: they see this one machine and nothing else on your tailnet. Then give them the three `local.properties` lines above.

**Use the model.** Same network access, plus `LLM_SHARE_KEY` and the `/v1` address above. No login on the server at all.

**Run the server.** Invite them to the tailnet, give them an account on the machine, and add them to the docker group:

```sh
sudo usermod -aG docker <user>
sudo usermod -aG nova <user>
```

The docker group is root in practice - anyone in it can mount the host filesystem into a container - so only do this for people you would trust with the whole server and everyone's data.

## Day to day

```sh
docker compose ps
docker compose logs -f nova     # the API log; model timings are on the [loop] lines
docker compose logs -f qwen     # the model server
bash update.sh                  # after copying new server code into ./server
bash backup.sh                  # database dump into data/backups (keeps 14)
docker compose restart qwen     # if the model wedges
```

To deploy new API code: pull the repo, copy `nova_v2/server` over `/srv/nova/server`, and run `bash update.sh`. It applies any schema additions and rebuilds only the API; the model and database keep running.

**From a teammate's own machine** (Windows, no login shell needed on the server beyond `nova-ops`), `redeploy.ps1` does all of that in one command. Set `NOVA_SSH=you@<server tailscale ip>` once, then:

```powershell
.\nova_v2\deploy\redeploy.ps1                 # your local nova_v2/server, after the tests pass
.\nova_v2\deploy\redeploy.ps1 -FromGitHub     # origin/main as pushed (-Branch for another)
```

It copies only what the image is built from (never `tests/`), keeps the team permissions (2770/660) so the next person can deploy, runs `sudo nova-ops deploy`, and waits for `/health`. `cat /srv/nova/server/.deployed` says what's live and who put it there. A plain `cp` or `tar` into `server/` leaves files only their owner can change; the next `sudo nova-ops deploy` puts the team permissions back (`update.sh` does it before building).

For a nightly backup, run `crontab -e` and add:

```
15 3 * * * bash /srv/nova/backup.sh >> /srv/nova/data/backups/backup.log 2>&1
```

To restore one:

```sh
docker compose exec -T db pg_restore -U supabase_admin -d postgres --clean /backups/<file>.dump
```

## Knobs (`.env`)

| Setting | Effect |
|---|---|
| `PUBLIC_URL` | The tailnet address. Must match the machine's real name |
| `QWEN_GPU` | Which GPU the model uses (default `1`; `0` on a single-GPU machine) |
| `QWEN_CTX` | The context preset (default `fast`). Raise it if long turns get cut off |
| `DISABLE_SIGNUP=true` | Stops new accounts from being created |
| `NOVA_API_KEY` | Must match the key built into the app (`local.properties`) |
| `LLM_SHARE_KEY` | The bearer key for the shared `/v1` model endpoint |
| `GOOGLE_MAPS_API_KEY` | Optional. Enables the navigation tool |

Restart after changing one: `docker compose up -d`.
