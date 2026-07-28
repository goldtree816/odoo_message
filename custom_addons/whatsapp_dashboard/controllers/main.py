import logging
import json
import re
import base64
import os
import mimetypes
import requests
from datetime import datetime
from requests.auth import HTTPBasicAuth
from odoo import http, fields, _
from odoo.http import request

# Import Odoo credentials from a local `config.py` (if present),
# otherwise fall back to environment variables.
#
# NOTE: ODOO_USER/ODOO_PASSWORD are no longer used now that FastAPI has been
# removed; kept here for backward compatibility with existing config.py files.
# WEBHOOK_BASE_URL is still used to build public URLs for attachments and
# the Twilio inbound webhook (e.g. your ngrok/public domain).
try:
    # Optional local config (not committed to git)
    from config import ODOO_USER, ODOO_PASSWORD, WEBHOOK_BASE_URL  # type: ignore
except Exception:
    # Fallback values so the module still works in dev/test.
    ODOO_USER = os.environ.get('ODOO_USER', 'admin')
    ODOO_PASSWORD = os.environ.get('ODOO_PASSWORD', 'admin')
    # Base URL exposed to the internet (e.g. ngrok) used for attachments.
    WEBHOOK_BASE_URL = os.environ.get('WEBHOOK_BASE_URL', 'https://jarring-oyster-happier.ngrok-free.dev')


_logger = logging.getLogger(__name__)

# ─── Twilio credentials ──────────────────────────────────────────
TWILIO_ACCOUNT_SID = 'AC5b39938c26320f5d6207df9b59e5d345'
TWILIO_AUTH_TOKEN = '136eed63d55fd5b4b6c3143f9d5cbebb'
TWILIO_FROM = 'whatsapp:+14155238886'
TWILIO_API_URL = (
    f'https://api.twilio.com/2010-04-01/Accounts/'
    f'{TWILIO_ACCOUNT_SID}/Messages.json'
)

# ─── Direct Twilio helpers (replaces the old FastAPI microservice) ──
# Everything the FastAPI service used to do (send message, list available
# numbers, buy a number, set the webhook) is now done directly from Odoo
# using the `requests` library and the Twilio REST API.

def _twilio_creds(subaccount=None):
    """Return (account_sid, auth_token) to use for a Twilio call.
    Only use the subaccount's credentials if they're confirmed real
    (came back from an actual Twilio API call) — never the locally
    generated placeholder ones used when Twilio subaccount creation failed."""
    if subaccount and subaccount.is_real_twilio_account \
            and subaccount.sid and subaccount.auth_token:
        return subaccount.sid, subaccount.auth_token
    return TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN


def _twilio_send_whatsapp_message(from_number, to_number, body, media_url=None, subaccount=None):
    """Send a WhatsApp message via the Twilio REST API. Returns the Twilio SID."""
    account_sid, auth_token = _twilio_creds(subaccount)

    if not from_number.startswith('whatsapp:'):
        from_number = f'whatsapp:{from_number}'
    if not to_number.startswith('whatsapp:'):
        to_number = f'whatsapp:{to_number}'

    url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json'
    data = {'From': from_number, 'To': to_number, 'Body': body or ''}
    if media_url:
        data['MediaUrl'] = media_url

    resp = requests.post(url, data=data, auth=HTTPBasicAuth(account_sid, auth_token), timeout=15)
    if resp.status_code in (200, 201):
        return resp.json().get('sid')
    _logger.error('Twilio send_message error %s: %s', resp.status_code, resp.text)
    raise Exception(f'Twilio error {resp.status_code}: {resp.text}')


def _twilio_available_numbers(country_code='US', number_type='local', limit=20, subaccount=None):
    """List purchasable numbers via the Twilio REST API."""
    account_sid, auth_token = _twilio_creds(subaccount)
    number_type = (number_type or 'local').capitalize()
    url = (
        f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}'
        f'/AvailablePhoneNumbers/{country_code}/{number_type}.json'
    )
    resp = requests.get(
        url, auth=HTTPBasicAuth(account_sid, auth_token),
        params={'PageSize': limit}, timeout=15
    )
    if resp.status_code != 200:
        raise Exception(f'Twilio error {resp.status_code}: {resp.text}')
    data = resp.json()
    numbers = []
    for num in data.get('available_phone_numbers', []):
        caps = [k.title() for k, v in (num.get('capabilities') or {}).items() if v]
        numbers.append({
            'id': num.get('phone_number'),
            'number': num.get('phone_number'),
            'display_number': num.get('friendly_name'),
            'type': number_type,
            'capabilities': ', '.join(caps),
            'monthlyCost': str(num.get('monthly_price', '0.00')),
            'setupFee': str(num.get('setup_fee', '0.00')),
        })
    return numbers


def _twilio_buy_number(phone_number, friendly_name=None, subaccount=None):
    """Purchase a phone number via the Twilio REST API. Returns dict with sid/phone_number."""
    account_sid, auth_token = _twilio_creds(subaccount)
    url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json'
    data = {'PhoneNumber': phone_number, 'FriendlyName': friendly_name or phone_number}
    resp = requests.post(url, data=data, auth=HTTPBasicAuth(account_sid, auth_token), timeout=30)
    if resp.status_code not in (200, 201):
        raise Exception(f'Twilio error {resp.status_code}: {resp.text}')
    return resp.json()


def _twilio_set_webhook(number_sid, webhook_url, subaccount=None):
    """Point an incoming number's SmsUrl at our own Odoo webhook route."""
    account_sid, auth_token = _twilio_creds(subaccount)
    url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers/{number_sid}.json'
    data = {'SmsUrl': webhook_url, 'SmsMethod': 'POST'}
    resp = requests.post(url, data=data, auth=HTTPBasicAuth(account_sid, auth_token), timeout=15)
    if resp.status_code in (200, 201):
        return True
    _logger.error('Failed to set Twilio webhook: %s', resp.text)
    return False


def _download_incoming_media(media_url, content_type=None, subaccount=None):
    """Download an inbound WhatsApp media file from Twilio and store it as a
    public ir.attachment. Returns the ir.attachment record, or None on failure.
    Twilio media URLs require HTTP Basic Auth with the account credentials."""
    if not media_url:
        return None
    account_sid, auth_token = _twilio_creds(subaccount)
    try:
        resp = requests.get(media_url, auth=HTTPBasicAuth(account_sid, auth_token), timeout=20)
        if resp.status_code != 200:
            _logger.error('Failed to download inbound media %s: %s %s',
                          media_url, resp.status_code, resp.text[:200])
            return None
        content_type = content_type or resp.headers.get('Content-Type', 'application/octet-stream')
        ext = mimetypes.guess_extension(content_type.split(';')[0].strip()) or ''
        filename = f"whatsapp_media_{int(datetime.now().timestamp())}{ext}"
        attachment = request.env['ir.attachment'].sudo().create({
            'name': filename,
            'datas': base64.b64encode(resp.content),
            'res_model': 'whatsapp.thread',
            'res_id': 0,
            'mimetype': content_type,
            'public': True,
        })
        return attachment
    except Exception as e:
        _logger.error('Exception downloading inbound media: %s', e)
        return None


# ─── Twilio subaccount helpers ──────────────────────────────────
def _create_twilio_subaccount(friendly_name):
    """Create a Twilio subaccount and return its SID and auth_token."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/SubAccounts.json"
    auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    data = {"FriendlyName": friendly_name}
    try:
        resp = requests.post(url, data=data, auth=auth, timeout=10)
        if resp.status_code in (200, 201):
            result = resp.json()
            return {
                "sid": result.get("sid"),
                "auth_token": result.get("auth_token"),
            }
        else:
            _logger.error("Twilio subaccount creation failed: %s", resp.text)
            return None
    except Exception as e:
        _logger.error("Twilio subaccount creation exception: %s", e)
        return None

def _close_twilio_subaccount(subaccount_sid):
    """Close a Twilio subaccount permanently."""
    if not subaccount_sid:
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/SubAccounts/{subaccount_sid}.json"
    auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    try:
        resp = requests.delete(url, auth=auth, timeout=10)
        if resp.status_code == 204:
            _logger.info("Twilio subaccount %s closed", subaccount_sid)
            return True
        else:
            _logger.error("Failed to close Twilio subaccount %s: %s", subaccount_sid, resp.text)
            return False
    except Exception as e:
        _logger.error("Exception closing Twilio subaccount: %s", e)
        return False

def _update_twilio_subaccount_status(subaccount_sid, new_status):
    """
    Update Twilio subaccount status.
    new_status must be 'active' or 'suspended' (or 'closed' but we handle close separately).
    """
    if not subaccount_sid:
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/SubAccounts/{subaccount_sid}.json"
    auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    data = {"Status": new_status.capitalize()}
    try:
        resp = requests.post(url, data=data, auth=auth, timeout=10)
        if resp.status_code in (200, 201):
            _logger.info("Twilio subaccount %s status updated to %s", subaccount_sid, new_status)
            return True
        else:
            _logger.error("Failed to update Twilio subaccount %s: %s", subaccount_sid, resp.text)
            return False
    except Exception as e:
        _logger.error("Exception updating Twilio subaccount: %s", e)
        return False


class WhatsAppDashboardController(http.Controller):

    # ── 1. Thread list ──────────────────────────────────────────────────
    @http.route('/whatsapp_dashboard/threads', type='jsonrpc', auth='user', methods=['POST'])
    def get_threads(self):
        threads = request.env['whatsapp.thread'].search([])
        return {'threads': [t.get_thread_data() for t in threads]}

    # ── 2. Messages for one thread ──────────────────────────────────────
    @http.route('/whatsapp_dashboard/messages', type='jsonrpc', auth='user', methods=['POST'])
    def get_messages(self, thread_id, **kwargs):
        msgs = request.env['whatsapp.message'].search([('thread_id', '=', int(thread_id))])
        return {'messages': [m.get_message_data() for m in msgs]}

    # ── 3. Mark thread as read ──────────────────────────────────────────
    @http.route('/whatsapp_dashboard/mark_read', type='jsonrpc', auth='user', methods=['POST'])
    def mark_read(self, thread_id, **kwargs):
        env = request.env
        unread = env['whatsapp.message'].search([
            ('thread_id', '=', int(thread_id)),
            ('direction', '=', 'incoming'),
            ('status', '!=', 'read'),
        ])
        unread.write({'status': 'read'})
        thread = env['whatsapp.thread'].browse(int(thread_id))
        if thread.exists():
            thread.unread_count = 0
        return {'success': True}

    # ── 4. Send message (saves locally + sends via Twilio directly) ────
    @http.route('/whatsapp_dashboard/send_message', type='jsonrpc', auth='user', methods=['POST'])
    def send_message(self, thread_id, body, msg_type, media_id=None, **kwargs):
        env = request.env
        thread = env['whatsapp.thread'].browse(int(thread_id))
        if not thread.exists():
            return {'error': 'Thread not found'}

        twilio_sid = None
        media_url = None
        send_status = 'sent'

        if msg_type == 'external':
            if not thread.phone:
                return {'error': 'Thread phone number is missing'}

            # 1. Generate the public URL for the attachment (if any)
            if media_id:
                attachment = env['ir.attachment'].sudo().browse(int(media_id))
                if attachment.exists() and attachment.datas:
                    # Ensure the attachment is public
                    if not attachment.public:
                        attachment.public = True
                    # Use the PUBLIC webhook base URL (ngrok) instead of localhost
                    media_url = f"{WEBHOOK_BASE_URL}/web/content/{attachment.id}?download=true"
                    _logger.info("Media URL for Twilio: %s", media_url)

            to_phone = thread.phone
            if to_phone.startswith('whatsapp:'):
                to_phone = to_phone.replace('whatsapp:', '').strip()

            # 2. Find the active sending number (needed as the "From" number)
            active_number = env['whatsapp.purchased_number'].sudo().search(
                [('is_sending_number', '=', True), ('status', '=', 'active')], limit=1
            )
            if not active_number:
                active_number = env['whatsapp.purchased_number'].sudo().search(
                    [('status', '=', 'active')], limit=1
                )
            if not active_number:
                return {'error': 'No active WhatsApp number found. Please purchase/activate one first.'}

            subaccount = active_number.subaccount_id if active_number.subaccount_id else None

            # 3. Call Twilio directly to send the message
            try:
                twilio_sid = _twilio_send_whatsapp_message(
                    active_number.number, to_phone, body or '', media_url, subaccount=subaccount
                )
                if twilio_sid:
                    _logger.info("Message sent via Twilio, SID: %s", twilio_sid)
                else:
                    _logger.error("Twilio send returned empty sid")
                    send_status = 'failed'
            except Exception as e:
                _logger.error("Twilio send error: %s", e)
                send_status = 'failed'
                return {'error': f"Failed to send: {e}"}

        # Create local message record
        # NOTE: body stays empty when it's an attachment-only message (no
        # caption typed) — the attachment card itself is shown in the UI,
        # so we must NOT also store the filename/placeholder as body text,
        # otherwise it renders twice (attachment card + text bubble).
        msg_vals = {
            'thread_id': thread.id,
            'body': body or '',
            'direction': 'outgoing',
            'message_type': msg_type,
            'status': 'sent',
            'timestamp': fields.Datetime.now(),
            'twilio_sid': twilio_sid,
        }
        if media_id:
            msg_vals['attachment_id'] = int(media_id)

        msg = env['whatsapp.message'].create(msg_vals)

        # Run spam detection on outgoing message
        spam_result = msg._detect_spam()
        msg.write({
            'is_spam': spam_result['is_spam'],
            'spam_score': spam_result['spam_score'],
            'spam_reasons': spam_result['spam_reasons'],
        })

        attachment_name = ''
        if media_id:
            att = env['ir.attachment'].sudo().browse(int(media_id))
            if att.exists():
                attachment_name = att.name or ''

        thread.write({
            'last_message': body[:200] or (f"📎 {attachment_name}" if attachment_name else ''),
            'last_message_date': fields.Datetime.now(),
        })
        return {
            'success': True,
            'message_id': msg.id,
            'message_data': msg.get_message_data(),
            'twilio_sid': twilio_sid,
        }

    # ── 5. Poll for new messages ────────────────────────────────────────
    @http.route('/whatsapp_dashboard/poll', type='jsonrpc', auth='user', methods=['POST'])
    def poll(self, thread_id, last_message_id, **kwargs):
        new_msgs = request.env['whatsapp.message'].search([
            ('thread_id', '=', int(thread_id)),
            ('id', '>', int(last_message_id)),
        ])
        threads = request.env['whatsapp.thread'].search([])
        return {
            'new_messages': [m.get_message_data() for m in new_msgs],
            'threads': [t.get_thread_data() for t in threads],
        }

    # ── 6. Upload media ─────────────────────────────────────────────────
    @http.route('/whatsapp_dashboard/upload_media', type='http', auth='user',
                methods=['POST'], csrf=False)
    def upload_media(self):
        env = request.env
        file = request.httprequest.files.get('file')
        if not file:
            return request.make_response(
                json.dumps({'error': 'No file provided'}),
                status=400,
                headers=[('Content-Type', 'application/json')]
            )
        file_data = file.read()
        if len(file_data) > 5 * 1024 * 1024:
            return request.make_response(
                json.dumps({'error': 'File exceeds 5 MB limit'}),
                status=400,
                headers=[('Content-Type', 'application/json')]
            )
        attachment = request.env['ir.attachment'].sudo().create({
            'name': file.filename,
            'datas': base64.b64encode(file_data),
            'res_model': 'whatsapp.thread',
            'res_id': 0,
            'mimetype': file.mimetype,
            'public': True,   # <-- important
        })
        # Use the public webhook base URL for media
        media_url = f"{WEBHOOK_BASE_URL}/web/content/{attachment.id}?download=true"
        return request.make_response(
            json.dumps({
                'attachment_id': attachment.id,
                'media_url': media_url,
            }),
            headers=[('Content-Type', 'application/json')]
        )

    # ── 7. Create a new external thread ────────────────────────────────
    @http.route('/whatsapp_dashboard/create_thread', type='jsonrpc', auth='user', methods=['POST'])
    def create_thread(self, name, phone, **kwargs):
        if not name or not phone:
            return {'error': 'Name and phone are required'}
        existing = request.env['whatsapp.thread'].search([('phone', '=', phone)], limit=1)
        if existing:
            return {'success': True, 'thread_data': existing.get_thread_data(), 'message': 'Thread already exists'}
        try:
            thread = request.env['whatsapp.thread'].create({
                'name': name,
                'phone': phone,
                'avatar_color': '#25D366',
                'status': 'offline',
                'thread_type': 'external',
                'last_message': 'New contact',
                'last_message_date': fields.Datetime.now(),
                'unread_count': 0,
            })
            return {'success': True, 'thread_data': thread.get_thread_data()}
        except Exception as e:
            _logger.error('Failed to create thread: %s', e)
            return {'error': str(e)}

    # ── 8. Fetch available phone numbers ────────────────────────────────
    @http.route('/whatsapp_dashboard/available_numbers', type='jsonrpc', auth='user', methods=['POST'])
    def available_numbers(self, country_code='US', number_type='local', subaccount_id=None, **kwargs):
        env = request.env
        subaccount = None
        if subaccount_id:
            subaccount = env['whatsapp.subaccount'].sudo().browse(int(subaccount_id))
            if not subaccount.exists():
                subaccount = None
        try:
            numbers = _twilio_available_numbers(
                country_code=country_code, number_type=number_type, limit=20, subaccount=subaccount
            )
            return {'numbers': numbers}
        except Exception as e:
            _logger.error('Twilio available_numbers error: %s', e)
            return {'error': str(e)}

    # ── 9. Purchase phone number (via Twilio directly) ─────────────────
    @http.route('/whatsapp_dashboard/purchase_number', type='jsonrpc', auth='user', methods=['POST'])
    def purchase_number(self, number, friendly_name=None, subaccount_id=None, **kwargs):
        env = request.env
        if not number:
            return {'error': 'Missing number'}
        clean_number = re.sub(r'[^\d+]', '', str(number).strip())
        if not clean_number.startswith('+'):
            clean_number = '+' + clean_number
        subaccount = None
        if subaccount_id:
            subaccount = env['whatsapp.subaccount'].sudo().browse(int(subaccount_id))
            if not subaccount.exists():
                subaccount = None
        try:
            result = _twilio_buy_number(clean_number, friendly_name or clean_number, subaccount=subaccount)
            number_sid = result.get('sid')
            purchased_number = result.get('phone_number', clean_number)

            # Point the number's webhook at our own Odoo inbound route
            webhook_url = f"{WEBHOOK_BASE_URL}/whatsapp_dashboard/webhook/inbound"
            webhook_ok = _twilio_set_webhook(number_sid, webhook_url, subaccount=subaccount)
            if not webhook_ok:
                _logger.warning("Could not set Twilio webhook for %s", purchased_number)

            purchased = env['whatsapp.purchased_number'].sudo().search(
                [('number', '=', purchased_number)], limit=1
            )
            if not purchased:
                purchased = env['whatsapp.purchased_number'].sudo().create({
                    'number': purchased_number,
                    'sid': number_sid or '',
                    'friendly_name': friendly_name or purchased_number,
                    'status': 'active',
                    'purchase_date': fields.Datetime.now(),
                    'subaccount_id': subaccount.id if subaccount else False,
                })
            if not env['whatsapp.purchased_number'].search([('is_sending_number', '=', True)], limit=1):
                purchased.write({'is_sending_number': True})
            return {
                'success': True,
                'number': purchased.number,
                'sid': purchased.sid,
                'friendly_name': purchased.friendly_name,
                'purchased_id': purchased.id,
                'webhook_set': webhook_ok,
            }
        except Exception as e:
            _logger.error("Twilio purchase error: %s", e)
            return {'error': str(e)}

    # ── 10. List purchased numbers ──────────────────────────────────────
    @http.route('/whatsapp_dashboard/purchased_numbers', type='jsonrpc', auth='user', methods=['POST'])
    def get_purchased_numbers(self, **kwargs):
        numbers = request.env['whatsapp.purchased_number'].search([])
        return {
            'numbers': [{
                'id': n.id,
                'number': n.number,
                'status': n.status.capitalize(),
                'sid': n.sid,
                'friendly_name': n.friendly_name,
                'purchase_date': n.purchase_date.strftime('%Y-%m-%d %H:%M') if n.purchase_date else '',
                'is_sending_number': bool(n.is_sending_number),
            } for n in numbers]
        }

    # ── 11. Toggle purchased number status ────────────────────────────
    @http.route('/whatsapp_dashboard/toggle_purchased_number_status', type='jsonrpc', auth='user', methods=['POST'])
    def toggle_purchased_number_status(self, number_id, **kwargs):
        env = request.env
        number = env['whatsapp.purchased_number'].browse(int(number_id))
        if not number.exists():
            return {'error': 'Number not found'}
        new_status = 'suspended' if number.status == 'active' else 'active'
        number.write({'status': new_status})
        if new_status == 'suspended' and number.is_sending_number:
            number.write({'is_sending_number': False})
            other = env['whatsapp.purchased_number'].search([('status', '=', 'active')], limit=1)
            if other:
                other.write({'is_sending_number': True})
        return {'success': True, 'new_status': new_status}

    # ── 12. Set active number for sending ────────────────────────────────
    @http.route('/whatsapp_dashboard/set_active_number', type='jsonrpc', auth='user', methods=['POST'])
    def set_active_number(self, number_id, **kwargs):
        env = request.env
        number = env['whatsapp.purchased_number'].browse(int(number_id))
        if not number.exists():
            return {'error': 'Number not found'}
        env['whatsapp.purchased_number'].search([]).write({'is_sending_number': False})
        number.write({'is_sending_number': True})
        return {'success': True}

    # ── 13. Delete a purchased number ────────────────────────────────────
    @http.route('/whatsapp_dashboard/delete_purchased_number', type='jsonrpc', auth='user', methods=['POST'])
    def delete_purchased_number(self, number_id, **kwargs):
        env = request.env
        number = env['whatsapp.purchased_number'].browse(int(number_id))
        if not number.exists():
            return {'error': 'Number not found'}
        was_active = number.is_sending_number
        number.unlink()
        if was_active:
            remaining = env['whatsapp.purchased_number'].search([('status', '=', 'active')], limit=1)
            if remaining:
                remaining.write({'is_sending_number': True})
        return {'success': True}

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC TWILIO WEBHOOK (replaces the old FastAPI /webhook/inbound)
    # ═══════════════════════════════════════════════════════════════════

    @http.route('/whatsapp_dashboard/webhook/inbound', type='http', auth='public',
                methods=['POST'], csrf=False)
    def webhook_inbound(self, **post):
        """Twilio calls this URL (set as the number's SmsUrl) whenever a
        WhatsApp message arrives. No auth – Twilio itself is calling it."""
        env = request.env
        from_raw = post.get('From', '') or ''
        to_raw = post.get('To', '') or ''
        body = post.get('Body', '') or ''
        message_sid = post.get('MessageSid')
        num_media = int(post.get('NumMedia', 0) or 0)
        media_url0 = post.get('MediaUrl0')
        media_content_type0 = post.get('MediaContentType0')

        phone = from_raw.replace('whatsapp:', '').strip()
        if not phone:
            return request.make_response(
                '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                headers=[('Content-Type', 'text/xml')]
            )

        # Figure out which of our numbers received this, so we can use the
        # right Twilio credentials (in case it belongs to a subaccount) when
        # downloading protected media URLs.
        to_phone = to_raw.replace('whatsapp:', '').strip()
        purchased_number = env['whatsapp.purchased_number'].sudo().search(
            [('number', '=', to_phone)], limit=1
        )
        subaccount = purchased_number.subaccount_id if purchased_number and purchased_number.subaccount_id else None

        # Download the first media item (if any) and attach it to the message.
        # Note: whatsapp.message only supports one attachment per message, so
        # if a WhatsApp user sends multiple files in one message, only the
        # first (MediaUrl0) is saved.
        attachment_id = False
        attachment_name = ''
        if num_media and media_url0:
            attachment = _download_incoming_media(media_url0, media_content_type0, subaccount=subaccount)
            if attachment:
                attachment_id = attachment.id
                attachment_name = attachment.name or ''
            else:
                _logger.warning('Could not download incoming media for message %s', message_sid)

        thread = env['whatsapp.thread'].sudo().search([('phone', '=', phone)], limit=1)
        if body:
            preview = body[:200]
        elif attachment_name:
            preview = f"📎 {attachment_name}"
        elif num_media:
            preview = '📎 Media message'
        else:
            preview = ''
        if thread:
            thread.write({
                'last_message': preview,
                'last_message_date': fields.Datetime.now(),
                'unread_count': thread.unread_count + 1,
                'status': 'online',
            })
        else:
            thread = env['whatsapp.thread'].sudo().create({
                'name': phone,
                'phone': phone,
                'avatar_color': '#25D366',
                'status': 'online',
                'thread_type': 'external',
                'last_message': preview,
                'last_message_date': fields.Datetime.now(),
                'unread_count': 1,
            })

        # NOTE: body stays empty when the incoming WhatsApp message is
        # media-only (no caption) — the attachment card already shows the
        # file, so storing a placeholder text here would render twice.
        msg_vals = {
            'thread_id': thread.id,
            'body': body or '',
            'direction': 'incoming',
            'message_type': 'external',
            'status': 'delivered',
            'timestamp': fields.Datetime.now(),
            'twilio_sid': message_sid,
        }
        if attachment_id:
            msg_vals['attachment_id'] = attachment_id
        msg = env['whatsapp.message'].sudo().create(msg_vals)

        # Run spam detection on the incoming message
        try:
            spam_result = msg._detect_spam()
            msg.write({
                'is_spam': spam_result['is_spam'],
                'spam_score': spam_result['spam_score'],
                'spam_reasons': spam_result['spam_reasons'],
            })
        except Exception as e:
            _logger.warning('Spam detection failed for incoming msg %s: %s', msg.id, e)

        _logger.info('Incoming WhatsApp message from %s saved to thread %s (media=%s)',
                     phone, thread.id, bool(attachment_id))
        return request.make_response(
            '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            headers=[('Content-Type', 'text/xml')]
        )

    # ═══════════════════════════════════════════════════════════════════
    # SUBACCOUNTS ROUTES (unchanged – they use direct Twilio calls)
    # ═══════════════════════════════════════════════════════════════════

    @http.route('/whatsapp_dashboard/subaccounts', type='jsonrpc', auth='user', methods=['POST'])
    def get_subaccounts(self, **kwargs):
        subaccounts = request.env['whatsapp.subaccount'].search([])
        return {
            'subaccounts': [s.get_subaccount_data() for s in subaccounts],
            'total': len(subaccounts),
            'total_phone_numbers': sum(s.phone_numbers_count for s in subaccounts),
            'total_sms_sent': sum(s.sms_sent_this_month for s in subaccounts),
            'total_voice_minutes': sum(s.voice_minutes_this_month for s in subaccounts),
        }

    @http.route('/whatsapp_dashboard/subaccount/send_otp', type='jsonrpc', auth='user', methods=['POST'])
    def send_subaccount_otp(self, email, **kwargs):
        """Generate + email a 6-digit OTP for the given email address.
        Must be verified via create_subaccount() before a subaccount is made."""
        email = (email or '').strip()
        if not email:
            return {'error': 'Email is required to send an OTP.'}
        try:
            request.env['whatsapp.subaccount.otp'].sudo().generate_and_send(
                email, purpose='subaccount_create'
            )
            return {'success': True, 'message': f'OTP sent to {email}.'}
        except ValueError as ve:
            return {'error': str(ve)}
        except Exception as exc:
            _logger.error("Failed to send subaccount OTP: %s", exc, exc_info=True)
            return {'error': 'Failed to send OTP. Please check the outgoing mail server configuration.'}

    @http.route('/whatsapp_dashboard/subaccount/create', type='jsonrpc', auth='user', methods=['POST'])
    def create_subaccount(self, name, unique_name, email='', otp='', status='active',
                          subaccount_type='standard', voice=True, sms=True,
                          mms=True, whatsapp=True, **kwargs):
        env = request.env

        name = (name or '').strip()
        unique_name = (unique_name or '').strip()
        email = (email or '').strip()

        if not name or not unique_name:
            return {'error': 'Friendly Name and Unique Name are required.'}

        if not email:
            return {'error': 'Email is required for OTP verification.'}

        # ── OTP MUST be validated before we touch Twilio / create anything ──
        try:
            env['whatsapp.subaccount.otp'].sudo().verify(email, otp, purpose='subaccount_create')
        except ValueError as ve:
            return {'error': str(ve)}

        existing = env['whatsapp.subaccount'].sudo().search(
            [('unique_name', '=', unique_name)], limit=1
        )
        if existing:
            return {'error': f'Unique Name "{unique_name}" is already in use.'}

        vals = {
            'name': name,
            'unique_name': unique_name,
            'email': email,
            'status': status,
            'subaccount_type': subaccount_type,
            'voice_enabled': voice,
            'sms_enabled': sms,
            'mms_enabled': mms,
            'whatsapp_enabled': whatsapp,
        }

        twilio_sid = None
        try:
            twilio_data = _create_twilio_subaccount(name)
            if twilio_data:
                vals['sid'] = twilio_data.get('sid')
                vals['auth_token'] = twilio_data.get('auth_token')
                twilio_sid = twilio_data.get('sid')
        except Exception as exc:
            # Twilio call failed/not implemented — fall back to a local-only
            # record; whatsapp.subaccount.create() already auto-generates a
            # placeholder sid/auth_token when none is supplied.
            _logger.warning("Twilio subaccount creation failed, using local fallback: %s", exc)

        subaccount = env['whatsapp.subaccount'].sudo().create(vals)

        return {
            'success': True,
            'subaccount': subaccount.get_subaccount_data(),
            'twilio_sid': twilio_sid,
        }

    @http.route('/whatsapp_dashboard/subaccount/update', type='jsonrpc', auth='user', methods=['POST'])
    def update_subaccount(self, subaccount_id, name=None, unique_name=None, email=None, status=None, **kwargs):
        env = request.env
        subaccount = env['whatsapp.subaccount'].sudo().browse(int(subaccount_id))
        if not subaccount.exists():
            return {'error': 'Subaccount not found'}

        vals = {}
        if name is not None:
            vals['name'] = name.strip()
        if unique_name is not None:
            unique_name = unique_name.strip()
            if unique_name != subaccount.unique_name:
                existing = env['whatsapp.subaccount'].sudo().search([('unique_name', '=', unique_name)], limit=1)
                if existing and existing.id != subaccount.id:
                    return {'error': 'Unique name already in use'}
                vals['unique_name'] = unique_name
        if email is not None:
            vals['email'] = email.strip()
        if status is not None and status in ('active', 'suspended'):
            vals['status'] = status

        if not vals:
            return {'error': 'No changes provided'}

        # If status is changing, also update Twilio
        if 'status' in vals and subaccount.sid:
            twilio_ok = _update_twilio_subaccount_status(subaccount.sid, vals['status'])
            if not twilio_ok:
                # Log error but continue – we'll update local anyway
                _logger.warning("Twilio status update failed for %s, but local will be updated", subaccount.sid)

        subaccount.write(vals)
        return {
            'success': True,
            'subaccount': subaccount.get_subaccount_data(),
            'message': 'Subaccount updated successfully'
        }

    @http.route('/whatsapp_dashboard/subaccount/delete', type='jsonrpc', auth='user', methods=['POST'])
    def delete_subaccount(self, subaccount_id, **kwargs):
        env = request.env
        subaccount = env['whatsapp.subaccount'].sudo().browse(int(subaccount_id))
        if not subaccount.exists():
            return {'error': 'Subaccount not found'}

        # Close on Twilio first
        if subaccount.sid:
            closed = _close_twilio_subaccount(subaccount.sid)
            if not closed:
                # If Twilio close fails, we may still want to delete locally? Better to abort?
                # For safety, we'll still delete locally but log error.
                _logger.error("Twilio close failed for %s, but local record will be removed", subaccount.sid)

        name = subaccount.name
        subaccount.unlink()
        return {'success': True, 'message': f'Subaccount "{name}" deleted successfully'}

    @http.route('/whatsapp_dashboard/subaccount/toggle_status', type='jsonrpc', auth='user', methods=['POST'])
    def toggle_subaccount_status(self, subaccount_id, **kwargs):
        env = request.env
        subaccount = env['whatsapp.subaccount'].sudo().browse(int(subaccount_id))
        if not subaccount.exists():
            return {'error': 'Subaccount not found'}

        new_status = 'suspended' if subaccount.status == 'active' else 'active'
        twilio_warning = None

        if subaccount.sid:
            twilio_ok = _update_twilio_subaccount_status(subaccount.sid, new_status)
            if not twilio_ok:
                twilio_warning = f"Twilio status update failed, but local status changed to {new_status}"

        subaccount.write({'status': new_status})
        return {
            'success': True,
            'subaccount': subaccount.get_subaccount_data(),
            'status': new_status,
            'warning': twilio_warning,
        }
