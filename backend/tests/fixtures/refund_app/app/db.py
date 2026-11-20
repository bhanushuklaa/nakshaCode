import psycopg


def lookup_order(order_id: str) -> dict:
    """Fetch the order row from Postgres."""
    with psycopg.connect("postgresql://localhost/shop") as conn:
        return conn.execute("SELECT * FROM orders WHERE id = %s", (order_id,)).fetchone()
