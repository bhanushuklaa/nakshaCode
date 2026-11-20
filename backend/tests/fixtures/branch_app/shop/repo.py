import psycopg

conn = psycopg.connect("postgresql://localhost/shop")


def find_order(order_id):
    return conn.execute("SELECT * FROM orders WHERE id = %s", (order_id,)).fetchone()


def reserve_stock(item):
    conn.execute("UPDATE stock SET qty = qty - 1 WHERE sku = %s", (item,))


def save_payment(receipt):
    conn.execute("INSERT INTO payments VALUES (%s)", (receipt,))
