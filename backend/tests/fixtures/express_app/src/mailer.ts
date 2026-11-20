import nodemailer from "nodemailer";

const transport = nodemailer.createTransport({});

export async function notifyCustomer(order: { email: string }) {
  await transport.sendMail({ to: order.email, subject: "Order received" });
}
