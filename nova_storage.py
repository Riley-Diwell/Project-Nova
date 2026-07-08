import argparse
import os
import secrets
import shutil
import sqlite3
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Config
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
LOCAL_DB = os.path.join(DATA_DIR, "local.db")
CLOUD_DB = os.path.join(DATA_DIR, "cloud_store.db")

PASSCODE = "Nova123" # temp hardcode

SEED_DATA = [
    (0, "Voice note - grocery list", "milk, eggs, bread, batteries"),
    (1, "Contact - Jamie Chen", "Jamie Chen, +61 4XX XXX XXX, jamie@example.com"),
    (1, "Contact - Alex Ollman", "Alex Ollman, alex@era.computer"),
    (1, "Calendar - Team standup", "Team standup, 2026-07-09 09:30, Zoom"),
    (1, "Calendar - Supervisor check-in", "Supervisor check-in, 2026-07-10 14:00, ANU"),
    (1, "Saved location - Home", "Home, -35.2809, 149.1300"),
    (1, "Health metric - resting HR", "resting_hr=58bpm,logged=2026-07-08T07:00"),
    (2, "Audio archive - voice memo 001", secrets.token_bytes(48_000)),
    (2, "Audio archive - voice memo 002", secrets.token_bytes(52_000)),
    (2, "Audio archive - voice memo 003", secrets.token_bytes(61_000)),
]

def derive_key(passcode: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390_000)
    return kdf.derive(passcode.encode("utf-8"))

def encrypt(key: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    nonce = secrets.token_bytes(12)
    return nonce, AESGCM(key).encrypt(nonce, plaintext, None)


def decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return AESGCM(key).decrypt(nonce, ciphertext, None)

# Setup
def get_or_create_salt(conn: sqlite3.Connection) -> bytes:
    conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value BLOB)")
    row = conn.execute("SELECT value FROM config WHERE key='salt'").fetchone()
    if row:
        return row[0]
    salt = secrets.token_bytes(16)
    conn.execute("INSERT INTO config (key, value) VALUES ('salt', ?)", (salt,))
    conn.commit()
    return salt

def init_local_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            tier INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            name_nonce BLOB NOT NULL,
            name_blob BLOB NOT NULL,
            content_nonce BLOB,
            content_blob BLOB,
            resident INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            tier INTEGER NOT NULL,
            outcome TEXT NOT NULL
        )
    """)
    conn.commit()

def init_cloud_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS archive (
            id INTEGER PRIMARY KEY,
            content_nonce BLOB NOT NULL,
            content_blob BLOB NOT NULL
        )
    """)
    conn.commit()

def decrypt_name(conn, key, item_id) -> str:
    nonce, blob = conn.execute(
        "SELECT name_nonce, name_blob FROM items WHERE id=?", (item_id,)
    ).fetchone()
    return decrypt(key, nonce, blob).decode("utf-8")

def audit(conn, actor, action, item_id, tier, outcome):
    """Store the audit fact - no readable name, ever, in this table."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, item_id, tier, outcome) VALUES (?,?,?,?,?,?)",
        (ts, actor, action, item_id, tier, outcome),
    )
    conn.commit()

def seed(local_conn, cloud_conn, key):
    for tier, name, content in SEED_DATA:
        name_nonce, name_blob = encrypt(key, name.encode("utf-8"))
        payload = content if isinstance(content, bytes) else content.encode("utf-8")
        added_at = datetime.now(timezone.utc).isoformat()

        if tier in (0, 1):
            content_nonce, content_blob = encrypt(key, payload)
            local_conn.execute(
                "INSERT INTO items (tier, added_at, name_nonce, name_blob, content_nonce, content_blob, resident) "
                "VALUES (?,?,?,?,?,?,1)",
                (tier, added_at, name_nonce, name_blob, content_nonce, content_blob),
            )
        else:
            cur = local_conn.execute(
                "INSERT INTO items (tier, added_at, name_nonce, name_blob, content_nonce, content_blob, resident) "
                "VALUES (?,?,?,?,NULL,NULL,0)",
                (tier, added_at, name_nonce, name_blob),
            )
            item_id = cur.lastrowid
            content_nonce, content_blob = encrypt(key, payload)
            cloud_conn.execute(
                "INSERT INTO archive (id, content_nonce, content_blob) VALUES (?,?,?)",
                (item_id, content_nonce, content_blob),
            )
    local_conn.commit()
    cloud_conn.commit()
    print(f"  seeded {len(SEED_DATA)} items (names encrypted locally regardless of tier)")

def report_footprint(local_conn):
    rows = local_conn.execute("SELECT tier, LENGTH(name_blob), LENGTH(content_blob), resident FROM items").fetchall()

    index_bytes = sum(r[1] for r in rows)
    tier0 = sum(r[2] or 0 for r in rows if r[0] == 0 and r[3])
    tier1 = sum(r[2] or 0 for r in rows if r[0] == 1 and r[3])
    must_be_resident = index_bytes + tier0 + tier1
    db_size = os.path.getsize(LOCAL_DB)

    print(f"\n  Item index (names, all tiers):  {index_bytes:>6} bytes encrypted, always local")
    print(f"  Tier 0 content (always device): {tier0:>6} bytes encrypted")
    print(f"  Tier 1 content (backed up):     {tier1:>6} bytes encrypted")
    print(f"  Must-be-resident total:         {must_be_resident:>6} bytes  <- floor for REQ1.1.1 sizing")
    print(f"  local.db file on disk:          {db_size:>6} bytes (includes audit log, salt, sqlite overhead)")

def read_item(local_conn, cloud_conn, key, item_id, network_available):
    name = decrypt_name(local_conn, key, item_id)
    tier, content_nonce, content_blob, resident = local_conn.execute(
        "SELECT tier, content_nonce, content_blob, resident FROM items WHERE id=?", (item_id,)
    ).fetchone()

    if resident:
        plaintext = decrypt(key, content_nonce, content_blob)
        print(f"  [read ] tier {tier}  '{name}'  -> {len(plaintext)} bytes, decrypted locally")
        audit(local_conn, "user", "read", item_id, tier, "success")
        return plaintext

    if not network_available:
        print(f"  [fetch] tier {tier}  '{name}'  -> no network, queued (index still readable offline)")
        audit(local_conn, "system", "fetch", item_id, tier, "queued")
        return None

    c_nonce, c_blob = cloud_conn.execute(
        "SELECT content_nonce, content_blob FROM archive WHERE id=?", (item_id,)
    ).fetchone()
    plaintext = decrypt(key, c_nonce, c_blob)
    print(f"  [fetch] tier {tier}  '{name}'  -> {len(plaintext)} bytes, fetched + decrypted over encrypted channel")
    audit(local_conn, "system", "fetch", item_id, tier, "success")
    return plaintext

def simulate_offline_then_online(local_conn, cloud_conn, key):
    ids = {tier: local_conn.execute("SELECT id FROM items WHERE tier=? LIMIT 1", (tier,)).fetchone()[0]
           for tier in (0, 1, 2)}

    print("\n  network: OFFLINE")
    for tier in (0, 1, 2):
        result = read_item(local_conn, cloud_conn, key, ids[tier], network_available=False)
        if tier in (0, 1):
            assert result is not None, "Tier 0/1 must never depend on network"

    print("\n  network: ONLINE")
    result = read_item(local_conn, cloud_conn, key, ids[2], network_available=True)
    assert result is not None, "Tier 2 must succeed once network returns"

def print_audit_log(local_conn, key):
    print("\nFull audit log for this run")
    print("  (names below are decrypted live for display - the table itself stores none)")
    rows = local_conn.execute(
        "SELECT ts, actor, action, item_id, tier, outcome FROM audit_log ORDER BY id"
    ).fetchall()
    for ts, actor, action, item_id, tier, outcome in rows:
        name = decrypt_name(local_conn, key, item_id)
        print(f"  [{ts}] {actor:<7} {action:<6} tier {tier}  '{name}'  -> {outcome}")
    print(f"  {len(rows)} actions logged - every read, fetch, and block accounted for")

def verify_encryption_at_rest():
    print("\nVerifying encryption at rest (no key involved)")
    needles = [b"Jamie Chen", b"jamie@example.com", b"Alex Ollman"]

    for path, label in [(LOCAL_DB, "local.db"), (CLOUD_DB, "cloud_store.db")]:
        with open(path, "rb") as f:
            raw = f.read()
        hits = [n for n in needles if n in raw]
        status = "FOUND (fail)" if hits else "not found (pass)"
        print(f"  Searching {label} raw bytes for plaintext PII... {status}")
        assert not hits, f"Plaintext leaked into {label}: {hits}"

def main():
    parser = argparse.ArgumentParser(description="Storage levels - working proof")
    parser.add_argument("--reset", action="store_true", help="wipe existing demo data and start fresh")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    if args.reset:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        print("(reset: cleared previous demo data)")

    fresh = not os.path.exists(LOCAL_DB)

    local_conn = sqlite3.connect(LOCAL_DB)
    cloud_conn = sqlite3.connect(CLOUD_DB)
    init_local_db(local_conn)
    init_cloud_db(cloud_conn)

    salt = get_or_create_salt(local_conn)
    key = derive_key(PASSCODE, salt)

    if fresh:
        seed(local_conn, cloud_conn, key)
    else:
        print("\n(existing data found - run with --reset to reseed fresh)")

    report_footprint(local_conn)
    simulate_offline_then_online(local_conn, cloud_conn, key)
    print_audit_log(local_conn, key)
    verify_encryption_at_rest()

    local_conn.close()
    cloud_conn.close()

if __name__ == "__main__":
    main()