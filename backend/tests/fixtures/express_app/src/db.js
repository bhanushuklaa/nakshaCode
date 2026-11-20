const { Pool } = require("pg");
const pool = new Pool();

async function saveOrder(order) {
  return pool.query("INSERT INTO orders VALUES ($1)", [order]);
}

async function findOrder(id) {
  return pool.query("SELECT * FROM orders WHERE id = $1", [id]);
}

module.exports = { saveOrder, findOrder };
