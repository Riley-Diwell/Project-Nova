#!/usr/bin/env bash
# Writes .env with fresh secrets. Only fills in what's missing, so re-running it
# never changes a key that's already in use.
#
# NOVA_API_KEY and GOOGLE_MAPS_API_KEY are copied in from the old server/.env
# (the app build has NOVA_API_KEY baked in): pass them in the environment,
#     NOVA_API_KEY=... GOOGLE_MAPS_API_KEY=... ./gen-keys.sh
set -euo pipefail
cd "$(dirname "$0")"

touch .env
chmod 600 .env

has() { grep -q "^$1=." .env; }
put() { has "$1" || echo "$1=$2" >> .env; }
rand() { openssl rand -hex "${1:-32}"; }

# An HS256 JWT signed with JWT_SECRET, as Supabase's anon/service_role keys are.
jwt() {
  python3 - "$1" "$2" <<'PY'
import base64, hashlib, hmac, json, sys, time
secret, role = sys.argv[1], sys.argv[2]
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
now = int(time.time())
body = b64(json.dumps({"role": role, "iss": "supabase", "iat": now, "exp": now + 10 * 365 * 86400}).encode())
sig = b64(hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
print(f"{header}.{body}.{sig}")
PY
}

put PUBLIC_URL "https://$(hostname).$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["MagicDNSSuffix"])' 2>/dev/null || echo local)"
put POSTGRES_PASSWORD "$(rand 24)"
put JWT_SECRET "$(rand 32)"
JWT_SECRET_VALUE="$(grep '^JWT_SECRET=' .env | cut -d= -f2-)"
put ANON_KEY "$(jwt "$JWT_SECRET_VALUE" anon)"
put SERVICE_ROLE_KEY "$(jwt "$JWT_SECRET_VALUE" service_role)"
put SEARXNG_SECRET "$(rand 32)"
put LLM_SHARE_KEY "nova-$(rand 20)"
put NOVA_API_KEY "${NOVA_API_KEY:-$(rand 24)}"
put GOOGLE_MAPS_API_KEY "${GOOGLE_MAPS_API_KEY:-}"
put LLM_MODEL qwen3.8-27b
put QWEN_GPU 1
put QWEN_CTX fast

echo "Wrote $(pwd)/.env ($(grep -c . .env) keys). Keep it private: chmod 600, never commit it."
