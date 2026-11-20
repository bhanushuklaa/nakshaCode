const Stripe = require("stripe");
const stripe = new Stripe("key");

async function chargeCard(order) { return stripe.paymentIntents.create({ amount: order.total }); }
async function refundCard(order) { return stripe.refunds.create({ payment_intent: order.pi }); }

module.exports = { chargeCard, refundCard };
