# Encrypted Vault

Live Polymarket credentials are stored in:

```text
credentials/encrypted_credentials.json
```

Create it once:

```bash
python setup_vault.py
```

During setup, choose `create` for a new Polymarket CLOB API credential set or
`derive` for an existing wallet-backed credential set. The refresh helper
`derive_polymarket_api_creds.py` only derives existing wallet-backed
credentials.

The vault stores:

- `POLYMARKET_PK`
- `POLYMARKET_FUNDER`
- `POLYMARKET_SIGNATURE_TYPE`
- `POLYMARKET_API_KEY`
- `POLYMARKET_API_SECRET`
- `POLYMARKET_PASSPHRASE`
- `POLYGON_RPC_URL`

Runtime settings such as Redis, sizing, order type, ledger paths, and log paths
stay in `.env`, the shell, or the systemd unit.

## Format

The outer JSON file contains only vault metadata and ciphertext:

```json
{
  "version": 2,
  "kdf": {
    "name": "argon2id",
    "salt": "<urlsafe-base64 salt>",
    "iterations": 3,
    "lanes": 4,
    "memory_cost": 65536,
    "length": 32
  },
  "encrypted": "<urlsafe-base64 Fernet token>"
}
```

The decrypted JSON payload has this shape:

```json
{
  "polymarket": {
    "private_key": "0x...",
    "funder": "0x...",
    "signature_type": 3,
    "api_key": "...",
    "api_secret": "...",
    "passphrase": "..."
  },
  "polygon_rpc_url": "https://provider.example/path/with-api-key"
}
```

The password is never stored. Losing it means the vault cannot be decrypted.
