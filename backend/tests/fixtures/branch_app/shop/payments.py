import stripe


class PaymentError(Exception):
    pass


def charge(order):
    return stripe.PaymentIntent.create(amount=order["total"])
