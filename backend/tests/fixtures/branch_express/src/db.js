const { Pool } = require("pg");
const pool = new Pool();

async function saveOrder(order) { return pool.query("INSERT INTO orders VALUES ($1)", [order]); }
async function reserve(item) { return pool.query("UPDATE stock SET qty = qty - 1 WHERE sku = $1", [item]); }

module.exports = { saveOrder, reserve };
