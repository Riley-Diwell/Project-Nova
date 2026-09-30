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

The address is `https://<host>.<your-tailnet>.ts.net`, published by `tailscale serve`. It works only from devices signed in to your tailnet — it is **not** exposed to the internet. The real address is in `.env` as `PUBLIC_URL`; this file keeps it out of the repo, which is public.

## First install

```sh
sudo bash setup-root.sh        # once: docker group, firewall, /srv/nova
./gen-keys.sh                  # writes .env - only fills in what's missing
./install.sh                   # applies the schema, starts everything, publishes it
```

Then, optionally, copy your data across from hosted Supabase:

```sh
./migrate-from-supabase.sh '<session pooler connection string>'
```

## The Android app

The app is built with the server address baked in, and neither the address nor the client key is in the repo. Each developer adds them once to `app/local.properties`:

```
NOVA_API_KEY=<the NOVA_API_KEY from .env>
NOVA_BASE_URL=https://<host>.<your-tailnet>.ts.net
NOVA_SERVER_IP=100.x.y.z
```

`NOVA_SERVER_IP` is the machine's Tailscale address. It is a fallback for the Android emulator, which cannot use Tailscale's MagicDNS but can still route to the tailnet through the host. On a real phone the name resolves normally, provided the Tailscale app is installed and signed in.

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

Three levels, smallest first.

**Just the model.** Give them `LLM_SHARE_KEY` and the `/v1` address above. No login on the server at all.

**Run and change NOVA.** This is the normal one:

```sh
sudo bash setup-team.sh alice
```

They get `sudo nova-ops ...`, write access to `server/` (the deployed API code), and read access to `.env`, which means the same database access the owner has. They do **not** get the docker group, and cannot change `compose.yml`, the Caddy config or the scripts — each of those can be turned into root on the whole machine. Finish in the Tailscale admin console: invite them, and add an SSH rule so they log in as their own user (`setup-team.sh` prints one).

```
sudo nova-ops status                what is running
sudo nova-ops logs nova 100         recent logs
sudo nova-ops follow qwen           stream a log
sudo nova-ops deploy                rebuild the API from server/ and restart
sudo nova-ops psql                  a session on the database
sudo nova-ops restart nova
sudo nova-ops backup
sudo nova-ops reconcile             re-run the Persona sweep for every user
```

Day to day nobody needs to edit files on the box: change code in the repo, copy it into `server/`, and run `sudo nova-ops deploy`.

**Full control of the machine.** `sudo usermod -aG docker <user>`. Note this is root in practice — anyone in the docker group can mount the host filesystem into a container. Only for people you would trust with the whole server and everyone's data.

> Anyone who can read `.env` or reach `psql` can read every user's memories, notes and episodes. That is the same access the owner has, which is the point, but it is worth saying out loud. And someone who can deploy API code can run code inside a container, so this is a firm boundary against mistakes rather than a guarantee against a determined attacker. If you need the harder line, give NOVA its own machine.

## Day to day

```sh
docker compose ps               # or: sudo nova-ops status
docker compose logs -f nova     # the API log; model timings are on the [loop] lines
docker compose logs -f qwen     # the model server
./update.sh                     # after copying new server code into ./server
./backup.sh                     # database dump into data/backups (keeps 14)
docker compose restart qwen     # if the model wedges
```

For a nightly backup, run `crontab -e` and add:

```
15 3 * * * /srv/nova/backup.sh >> /srv/nova/data/backups/backup.log 2>&1
```

To restore one:

```sh
docker compose exec -T db pg_restore -U supabase_admin -d postgres --clean /backups/<file>.dump
```

## Knobs (`.env`)

| Setting | Effect |
|---|---|
| `QWEN_GPU` | Which GPU the model uses (default `1`) |
| `QWEN_CTX` | The context preset (default `fast`). Raise it if long turns get cut off |
| `DISABLE_SIGNUP=true` | Stops new accounts from being created |
| `NOVA_API_KEY` | Must match the key built into the app (`local.properties`) |
| `LLM_SHARE_KEY` | The bearer key for the shared `/v1` model endpoint |
