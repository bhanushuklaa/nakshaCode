const express = require("express");
const { saveOrder, reserve } = require("./db");
const { chargeCard, refundCard } = require("./pay");

const app = express();

app.post("/orders", async (req, res) => {
  const order = await saveOrder(req.body);
  if (!order) {
    return res.status(400).json({ error: "invalid" });
  }
  try {
    await chargeCard(order);
  } catch (err) {
    await refundCard(order);
    throw err;
  }
  order.items.forEach((item) => reserve(item));
  res.json(order);
});
