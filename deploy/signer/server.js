"use strict";

const express = require("express");
const { createWalletClient, createPublicClient, http } = require("viem");
const { privateKeyToAccount } = require("viem/accounts");
const { polygon } = require("viem/chains");
const { ClobClient, Chain, OrderType, Side, SignatureTypeV2, AssetType } = require("@polymarket/clob-client-v2");

const app = express();
app.use(express.json());

const PORT = parseInt(process.env.SIGNER_PORT || "7777", 10);
const HOST = "https://clob.polymarket.com";
// polygon-rpc.com now returns 401 ("tenant disabled"); use a keyless public RPC
// by default.  Order signing is off-chain and does not need this, but the
// /balance endpoint and the boot-time owner self-check do.
const RPC_URL = process.env.POLYMARKET_SIGNER_RPC_URL || "https://polygon-bor-rpc.publicnode.com";

const PRIVATE_KEY = process.env.POLYMARKET_SIGNER_PRIVATE_KEY;
const API_KEY = process.env.POLYMARKET_SIGNER_API_KEY;
const API_SECRET = process.env.POLYMARKET_SIGNER_API_SECRET;
const API_PASSPHRASE = process.env.POLYMARKET_SIGNER_API_PASSPHRASE;
const FUNDER = process.env.POLYMARKET_SIGNER_FUNDER;
const SIGNATURE_TYPE_RAW = process.env.POLYMARKET_SIGNER_SIGNATURE_TYPE || "1";

function resolveSignatureType(rawValue) {
  const raw = String(rawValue || "1").trim();
  if (/^[0-3]$/.test(raw)) return parseInt(raw, 10);
  const key = raw.toUpperCase();
  if (SignatureTypeV2[key] !== undefined) return SignatureTypeV2[key];
  throw new Error(`Unsupported POLYMARKET_SIGNER_SIGNATURE_TYPE: ${rawValue}`);
}

const SIGNATURE_TYPE = resolveSignatureType(SIGNATURE_TYPE_RAW);

if (!PRIVATE_KEY) {
  console.error("POLYMARKET_SIGNER_PRIVATE_KEY is required");
  process.exit(1);
}

function normalizePrivateKey(rawValue) {
  const raw = String(rawValue || "").trim();
  return raw.startsWith("0x") ? raw : `0x${raw}`;
}

// Derive the signer EOA address once at boot so it can be logged, exposed on
// /health, and checked against the funder's owner.  This is the address the
// CLOB requires the API key to belong to.
const SIGNER_ADDRESS = privateKeyToAccount(normalizePrivateKey(PRIVATE_KEY)).address;

let client;
function getClient() {
  if (client) return client;
  const account = privateKeyToAccount(normalizePrivateKey(PRIVATE_KEY));
  const walletClient = createWalletClient({ account, chain: polygon, transport: http(RPC_URL) });

  // clob-client-v2 expects ApiKeyCreds = { key, secret, passphrase }
  // (NOT the snake_case api_key/api_secret/api_passphrase used by the v1 client).
  const creds = API_KEY ? {
    key: API_KEY,
    secret: API_SECRET || "",
    passphrase: API_PASSPHRASE || "",
  } : undefined;
  client = new ClobClient({
    host: HOST,
    chain: Chain.POLYGON,
    signer: walletClient,
    creds,
    signatureType: SIGNATURE_TYPE,
    funderAddress: FUNDER || undefined,
  });
  return client;
}

app.post("/order", async (req, res) => {
  const { token_id, price, size, side, tick_size = "0.01", neg_risk = false } = req.body;
  if (!token_id || price == null || size == null || !side) {
    return res.status(400).json({ error: "Missing required fields: token_id, price, size, side" });
  }
  if (!["buy", "sell"].includes(side.toLowerCase())) {
    return res.status(400).json({ error: `Invalid side: ${side}` });
  }
  try {
    const c = getClient();
    const orderSide = side.toLowerCase() === "buy" ? Side.BUY : Side.SELL;
    const resp = await c.createAndPostOrder(
      { tokenID: token_id, price: parseFloat(price), size: parseFloat(size), side: orderSide },
      { tickSize: tick_size, negRisk: neg_risk },
      OrderType.GTC,
    );
    console.log(`[signer] order posted: ${side} ${size} @ ${price} token=${token_id.slice(0, 8)}...`);
    return res.json(resp);
  } catch (err) {
    console.error(`[signer] order failed: ${err.message}`);
    return res.status(400).json({ error: err.message, raw: err.toString() });
  }
});

app.get("/health", (req, res) => {
  res.json({
    status: "ok",
    signer: SIGNER_ADDRESS,
    funder: FUNDER || "not set",
    signatureType: SIGNATURE_TYPE,
  });
});

app.get("/balance", async (req, res) => {
  try {
    const c = getClient();
    // clob-client-v2 exposes getBalanceAllowance (there is no getBalance).
    // COLLATERAL = USDC balance for the configured funder.
    const balance = await c.getBalanceAllowance({ asset_type: AssetType.COLLATERAL });
    return res.json({ balance });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

// Best-effort boot-time sanity check.  The most common live-trading
// misconfiguration is a signer key / API key / funder that resolve to different
// EOAs, which the CLOB rejects only at order time with a cryptic message.  We
// surface it loudly at startup instead.  Never fatal — purely diagnostic.
async function selfCheck() {
  console.log(`[signer] signer EOA=${SIGNER_ADDRESS}`);
  if (!FUNDER) return;
  if (FUNDER.toLowerCase() === SIGNER_ADDRESS.toLowerCase()) return; // EOA funder
  try {
    const pc = createPublicClient({ chain: polygon, transport: http(RPC_URL) });
    let owner;
    try {
      owner = await pc.readContract({
        address: FUNDER,
        abi: [{ name: "owner", type: "function", stateMutability: "view", inputs: [], outputs: [{ type: "address" }] }],
        functionName: "owner",
      });
    } catch {
      // Gnosis-Safe style funders expose getOwners() instead of owner().
      try {
        const owners = await pc.readContract({
          address: FUNDER,
          abi: [{ name: "getOwners", type: "function", stateMutability: "view", inputs: [], outputs: [{ type: "address[]" }] }],
          functionName: "getOwners",
        });
        owner = owners.find((o) => o.toLowerCase() === SIGNER_ADDRESS.toLowerCase()) || owners[0];
      } catch {
        console.log(`[signer] WARN could not read funder ${FUNDER} owner() for self-check (skipping)`);
        return;
      }
    }
    if (owner && owner.toLowerCase() === SIGNER_ADDRESS.toLowerCase()) {
      console.log(`[signer] OK funder ${FUNDER} is owned by signer EOA`);
    } else {
      console.error(
        `[signer] CONFIG ERROR: funder ${FUNDER} owner is ${owner}, but signer EOA is ` +
        `${SIGNER_ADDRESS}. The signer key is NOT the owner of this deposit wallet — ` +
        `orders will be rejected. Set POLYMARKET_SIGNER_PRIVATE_KEY to the proxy owner's key.`,
      );
    }
  } catch (err) {
    console.log(`[signer] WARN self-check skipped: ${err.message}`);
  }
}

app.listen(PORT, "0.0.0.0", () => {
  console.log(`[signer] running on port ${PORT}`);
  console.log(`[signer] funder=${FUNDER || "not set"}`);
  console.log(`[signer] signatureType=${SIGNATURE_TYPE}`);
  selfCheck();
});
