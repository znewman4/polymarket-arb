"use strict";

const express = require("express");
const { createWalletClient, http } = require("viem");
const { privateKeyToAccount } = require("viem/accounts");
const { polygon } = require("viem/chains");
const { ClobClient, Chain, OrderType, Side, SignatureTypeV2 } = require("@polymarket/clob-client-v2");

const app = express();
app.use(express.json());

const PORT = parseInt(process.env.SIGNER_PORT || "7777", 10);
const HOST = "https://clob.polymarket.com";

const PRIVATE_KEY = process.env.POLYMARKET_SIGNER_PRIVATE_KEY;
const API_KEY = process.env.POLYMARKET_SIGNER_API_KEY;
const API_SECRET = process.env.POLYMARKET_SIGNER_API_SECRET;
const API_PASSPHRASE = process.env.POLYMARKET_SIGNER_API_PASSPHRASE;
const FUNDER = process.env.POLYMARKET_SIGNER_FUNDER;

if (!PRIVATE_KEY) {
  console.error("POLYMARKET_SIGNER_PRIVATE_KEY is required");
  process.exit(1);
}

let client;
function getClient() {
  if (client) return client;
  const account = privateKeyToAccount(PRIVATE_KEY);
  const walletClient = createWalletClient({ account, chain: polygon, transport: http("https://polygon-rpc.com") });

  const creds = API_KEY ? {
    api_key: API_KEY,
    api_secret: API_SECRET || "",
    api_passphrase: API_PASSPHRASE || "",
  } : undefined;
  client = new ClobClient({
    host: HOST,
    chain: Chain.POLYGON,
    signer: walletClient,
    creds,
    signatureType: SignatureTypeV2.POLY_PROXY,
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
  res.json({ status: "ok", funder: FUNDER || "not set" });
});

app.get("/balance", async (req, res) => {
  try {
    const c = getClient();
    const balance = await c.getBalance();
    return res.json({ balance });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`[signer] running on port ${PORT}`);
  console.log(`[signer] funder=${FUNDER || "not set"}`);
});
