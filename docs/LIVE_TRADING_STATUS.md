# Live Trading Status

## Confirmed working (2026-06-02)

### Limitless leg

- Live orders submit successfully via EIP-712 signing on Base chain
- `owner_id=1364477` resolves correctly
- USDC approval flow works (per-exchange, cached)
- Confirmed order IDs: e.g. `bcfc886a-765d-4b7c-aa37-eb035787fca3` (OpenSea, 2026-06-02)

### Polymarket leg

- Live orders submit via Node.js poly-signer microservice (`deploy/signer/server.js`)
- Signature type: `POLY_1271` (type 3) - deposit wallet flow
- Signer EOA: `0x64e8a3549Dbb8e097dfB2C23F1a59B62D7DE2474`
- Funder/deposit wallet: `0xa9b63624DE56FD861b6a33E2a7Fce53637392Aae`
- API key owner verified against funder on boot
- Confirmed order accepted: `success:true`, `status: live` (2026-06-01, commit f9d3d80)

### Known limitations before scaling up

- Exit (sell) legs: implemented in this PR - verify in paper mode first
- Stake size: currently $1 per leg - increase after paper PnL confirmed
- Polymarket minimum order size: $5 on some markets - bot enforces per-market tick size

## Tests to run before pushing

```bash
pytest tests/test_limitless/ tests/test_live/ tests/test_safety/ -q
ruff check src/ tests/
```

All must pass.
