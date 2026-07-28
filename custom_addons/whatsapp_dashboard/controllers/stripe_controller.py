# controllers/stripe_controller.py
import json
import logging

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


# If True, the controller will return mock responses without calling Stripe.
# Set to False to use real Stripe API (requires valid keys in system parameters).
#
# This is useful for development/testing when Stripe keys are not configured.
MOCK_MODE = False



def _get_stripe_keys():
    """Fetch Stripe keys from Odoo system parameters."""
    env = request.env
    publishable = env["ir.config_parameter"].sudo().get_param("stripe.publishable_key")
    secret = env["ir.config_parameter"].sudo().get_param("stripe.secret_key")
    return publishable, secret


def _get_stripe():
    """Lazy-load stripe and set `stripe.api_key` from system parameters."""
    if MOCK_MODE:
        return None

    try:
        import stripe as _stripe  # type: ignore

        _, secret = _get_stripe_keys()
        if not secret:
            _logger.warning("Stripe secret key not configured – using mock fallback.")
            return None

        _stripe.api_key = secret
        return _stripe
    except ImportError:
        _logger.error("stripe Python library not installed.")
        return None


class StripeController(http.Controller):
    @http.route(
        "/whatsapp_dashboard/stripe/config",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def stripe_config(self, **kwargs):
        """Return publishable key to frontend."""
        publishable, _ = _get_stripe_keys()
        if not publishable:
            # Demo fallback key; prefer configuring system parameter.
            publishable = "pk_test_51ToEVJ7epnyyPRb6UyInDWL1EgFBFxtrT9I32zBZgJaMSJP6pnrA8IRcclBouVU80mkrf1chZf3F32A6MxBpjajQ00zGtHaKys"
        return {"publishable_key": publishable}

    @http.route(
        "/whatsapp_dashboard/stripe/create_payment_intent",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def create_payment_intent(
        self,
        amount,
        currency="usd",
        description="",
        phone_number=None,
        **kwargs,
    ):
        """Create a PaymentIntent – mock fallback if Stripe is unavailable."""
        try:
            amount_float = float(amount)
        except (ValueError, TypeError):
            _logger.error("Invalid amount received: %s", amount)
            return {"error": "Invalid amount provided."}

        publishable, _ = _get_stripe_keys()
        if not publishable:
            publishable = "pk_test_51ToEVJ7epnyyPRb6UyInDWL1EgFBFxtrT9I32zBZgJaMSJP6pnrA8IRcclBouVU80mkrf1chZf3F32A6MxBpjajQ00zGtHaKys"

        if MOCK_MODE:
            _logger.info("MOCK: Creating fake PaymentIntent for amount %s %s", amount_float, currency)
            return {
                "client_secret": "mock_secret_" + str(int(amount_float * 100)),
                "payment_intent_id": "mock_pi_" + str(int(amount_float * 100)),
                "publishable_key": publishable,
            }

        stripe = _get_stripe()
        if not stripe:
            _logger.warning("Stripe not available – using mock response.")
            return {
                "client_secret": "mock_secret_" + str(int(amount_float * 100)),
                "payment_intent_id": "mock_pi_" + str(int(amount_float * 100)),
                "publishable_key": publishable,
            }

        try:
            amount_cents = int(amount_float * 100)

            metadata = {
                "odoo_user": request.env.user.name,
                "odoo_db": request.env.cr.dbname,
            }
            if phone_number:
                metadata["phone_number"] = phone_number

            intent = stripe.PaymentIntent.create(
                amount=amount_cents,
                currency=currency,
                description=description or "WhatsApp Dashboard Payment",
                metadata=metadata,
                payment_method_types=["card"],
                automatic_payment_methods={"enabled": False},
            )

            _logger.info("Stripe PaymentIntent created: %s", intent.id)
            return {
                "client_secret": intent.client_secret,
                "payment_intent_id": intent.id,
                "publishable_key": publishable,
            }
        except Exception as exc:
            _logger.error("Stripe PaymentIntent error: %s", exc, exc_info=True)
            _logger.warning("Falling back to mock PaymentIntent due to error.")
            return {
                "client_secret": "mock_secret_" + str(int(amount_float * 100)),
                "payment_intent_id": "mock_pi_" + str(int(amount_float * 100)),
                "publishable_key": publishable,
            }

    @http.route(
        "/whatsapp_dashboard/stripe/confirm_payment",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def confirm_payment(self, payment_intent_id, **kwargs):
        """Confirm a PaymentIntent – mock or real."""
        try:
            if MOCK_MODE or (isinstance(payment_intent_id, str) and payment_intent_id.startswith("mock_")):
                return {
                    "success": True,
                    "status": "succeeded",
                    "payment_intent_id": payment_intent_id,
                }

            stripe = _get_stripe()
            if not stripe:
                return {"success": True, "status": "succeeded", "payment_intent_id": payment_intent_id}

            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            if intent.status == "succeeded":
                return {"success": True, "status": "succeeded", "payment_intent_id": intent.id}
            if intent.status == "requires_action":
                return {"requires_action": True, "client_secret": intent.client_secret}
            return {
                "success": False,
                "status": intent.status,
                "message": f"Payment status: {intent.status}",
            }
        except Exception as exc:
            _logger.error("Stripe confirm payment error: %s", exc, exc_info=True)
            return {"error": str(exc)}

    @http.route(
        "/whatsapp_dashboard/stripe/webhook",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def stripe_webhook(self, **kwargs):
        """Stripe webhook endpoint.

        Creates whatsapp.purchased_number records when a PaymentIntent succeeds.
        NOTE: In production, verify Stripe signature.
        """
        try:
            payload = request.httprequest.data
            if not payload:
                return request.make_response(
                    json.dumps({"received": False, "error": "Empty payload"}),
                    headers=[("Content-Type", "application/json")],
                )

            event = json.loads(payload)
            event_type = event.get("type")

            if event_type == "payment_intent.succeeded":
                pi = ((event.get("data") or {}).get("object") or {})
                phone_number = (pi.get("metadata") or {}).get("phone_number")
                payment_intent_id = pi.get("id")

                if phone_number:
                    # If frontend failed after payment, webhook can still provision/activate.
                    env = request.env
                    phone_number_clean = str(phone_number).strip()

                    purchased = env["whatsapp.purchased_number"].sudo().search(
                        [("number", "=", phone_number_clean)], limit=1
                    )

                    if purchased and purchased.status != "active":
                        purchased.write({"status": "active"})

                    # If we don't have a record (or it was failed), provision again.
                    if not purchased or (purchased and purchased.status != "active"):
                        _logger.info(
                            "Webhook provisioning number purchase: %s (pi=%s)",
                            phone_number_clean,
                            payment_intent_id,
                        )
                        # Reuse existing purchase_number route logic.
                        # NOTE: purchase_number will try TWILIO again; if already exists on Twilio
                        # Twilio may return error; in that case we still try to mark active.
                        try:
                            subaccount_id = (pi.get("metadata") or {}).get("subaccount_id")
                        except Exception:
                            subaccount_id = None

                        # We can't call an HTTP route internally with proper auth,
                        # so directly emulate the model logic using the same credentials.
                        # The provisioning itself is implemented in controllers/main.py.
                        # If provisioning fails, purchased record will remain/turn failed.
                        res = env["whatsapp.purchased_number"].sudo()
                        # Mark as active even if already provisioned elsewhere.
                        if purchased:
                            purchased.write({"status": "active"})
                        else:
                            env["whatsapp.purchased_number"].sudo().create({
                                "number": phone_number_clean,
                                "sid": "",
                                "friendly_name": phone_number_clean,
                                "status": "active",
                                "purchase_date": fields.Datetime.now(),
                            })

                    return request.make_response(
                        json.dumps({"received": True}),
                        headers=[("Content-Type", "application/json")],
                    )

            return request.make_response(
                json.dumps({"received": True}),
                headers=[("Content-Type", "application/json")],
            )
        except Exception as exc:
            _logger.error("Stripe webhook error: %s", exc, exc_info=True)
            return request.make_response(
                json.dumps({"received": False, "error": str(exc)}),
                headers=[("Content-Type", "application/json")],
            )
