const express = require("express");
const { saveOrder } = require("./db");
import { notifyCustomer } from "./mailer";

const app = express();

app.post("/orders", async (req, res) => {
  const order = await saveOrder(req.body);
  await notifyCustomer(order);
  res.json(order);
});

app.get("/orders/:id", getOrder);

async function getOrder(req, res) {
  res.json(await findOrder(req.params.id));
}

module.exports = app;
