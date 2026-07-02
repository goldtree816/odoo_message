# controllers/stripe_controller.py
import json
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Stripe keys - move to system parameters in production
STRIPE_PUBLISHABLE_KEY = 'pk_test_51ToEVJ7epnyyPRb6UyInDWLIeGFBFxtrT9I32zBZgJaMSJP6pnrA8IRcclBouVU80mkrflchZf3F32A6MxBpjaJQ00zGtHaKys'
STRIPE_SECRET_KEY = 'sk_test_51ToEVJ7epnyyPRb6eO15RP2yNsmyi6cYGhWeM3zNwMalwSnMKtfj21yUpDAuD5ldikv65mbWkXsPj6'

def _get_stripe():
    """Lazy-import stripe so the module still loads if the lib is missing."""
    try:
        import stripe as _stripe # type: ignore
        _stripe.api_key = STRIPE_SECRET_KEY
        return _stripe
    except ImportError:
        _logger.error('stripe Python library not installed.')
        return None

@http.route('/whatsapp_dashboard/stripe/config', type='json', auth='user', methods=['POST'])
def stripe_config(self, **kwargs):
    """Return publishable key to frontend"""
    return {'publishable_key': STRIPE_PUBLISHABLE_KEY}

@http.route('/whatsapp_dashboard/stripe/create_payment_intent', type='json', auth='user', methods=['POST'])
def create_payment_intent(self, amount, currency='usd', description='', **kwargs):
    """Create a PaymentIntent for one-time payment"""
    stripe = _get_stripe()
    if not stripe:
        return {'error': 'Stripe library not installed on server'}

    try:
        # amount must be in cents
        amount_cents = int(float(amount) * 100)
        
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency,
            description=description or 'WhatsApp Dashboard Payment',
            metadata={
                'odoo_user': request.env.user.name,
                'odoo_db': request.env.cr.dbname,
            },
            automatic_payment_methods={'enabled': True},
        )
        
        _logger.info('Stripe PaymentIntent created: %s', intent.id)
        
        return {
            'client_secret': intent.client_secret,
            'payment_intent_id': intent.id,
            'publishable_key': STRIPE_PUBLISHABLE_KEY,
        }
    except Exception as exc:
        _logger.error('Stripe PaymentIntent error: %s', exc)
        return {'error': str(exc)}

@http.route('/whatsapp_dashboard/stripe/confirm_payment', type='json', auth='user', methods=['POST'])
def confirm_payment(self, payment_intent_id, **kwargs):
    """Confirm a PaymentIntent (for 3D Secure or additional verification)"""
    stripe = _get_stripe()
    if not stripe:
        return {'error': 'Stripe library not installed'}

    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        
        # Check if payment succeeded
        if intent.status == 'succeeded':
            return {
                'success': True,
                'status': 'succeeded',
                'payment_intent_id': intent.id
            }
        elif intent.status == 'requires_action':
            return {
                'requires_action': True,
                'client_secret': intent.client_secret
            }
        else:
            return {
                'success': False,
                'status': intent.status,
                'message': f'Payment status: {intent.status}'
            }
    except Exception as exc:
        _logger.error('Stripe confirm payment error: %s', exc)
        return {'error': str(exc)}

@http.route('/whatsapp_dashboard/stripe/webhook', type='http', auth='public', methods=['POST'], csrf=False)
def stripe_webhook(self, **kwargs):
    """Stripe webhook endpoint"""
    stripe = _get_stripe()
    payload = request.httprequest.data
    sig_header = request.httprequest.headers.get('Stripe-Signature', '')

    # Set webhook secret in system parameters
    webhook_secret = request.env['ir.config_parameter'].sudo().get_param('stripe.webhook_secret', '')

    event = None
    if webhook_secret and stripe:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except Exception as exc:
            _logger.warning('Stripe webhook signature error: %s', exc)
            return request.make_response('Invalid signature', status=400, headers=[('Content-Type', 'text/plain')])
    else:
        try:
            event = json.loads(payload)
        except Exception:
            return request.make_response('Bad payload', status=400, headers=[('Content-Type', 'text/plain')])

    event_type = event.get('type', '')
    _logger.info('Stripe webhook received: %s', event_type)

    if event_type == 'payment_intent.succeeded':
        pi = event['data']['object']
        _logger.info('Payment succeeded: %s amount: %s %s', pi['id'], pi['amount'] / 100, pi['currency'].upper())
        # Add your business logic here (e.g., activate phone number, create subscription, etc.)
    elif event_type == 'invoice.paid':
        inv = event['data']['object']
        _logger.info('Invoice paid: %s', inv['id'])
    elif event_type == 'customer.subscription.deleted':
        sub = event['data']['object']
        _logger.info('Subscription cancelled: %s', sub['id'])

    return request.make_response(json.dumps({'received': True}), headers=[('Content-Type', 'application/json')])