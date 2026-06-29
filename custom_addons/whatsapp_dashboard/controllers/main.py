import logging
import json
import base64
import requests
from requests.auth import HTTPBasicAuth

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)

# ── Twilio sandbox credentials ──────────────────────────────────────────────
TWILIO_ACCOUNT_SID = 'AC5b39938c26320f5d6207df9b59e5d345'
TWILIO_AUTH_TOKEN  = 'cb49c04cb21377148d6f8d6b2ad26543'
TWILIO_FROM        = 'whatsapp:+14155238886'   # Twilio sandbox number
TWILIO_API_URL     = (
    f'https://api.twilio.com/2010-04-01/Accounts/'
    f'{TWILIO_ACCOUNT_SID}/Messages.json'
)


def _send_via_twilio(to_phone, body, media_url=None):
    """
    POST an outgoing WhatsApp message through the Twilio sandbox.
    If media_url is provided, it sends a media message with optional body.
    """
    to_wa = f'whatsapp:{to_phone}' if not to_phone.startswith('whatsapp:') else to_phone
    data = {
        'From': TWILIO_FROM,
        'To': to_wa,
        'Body': body,
    }
    if media_url:
        data['MediaUrl'] = media_url

    try:
        resp = requests.post(
            TWILIO_API_URL,
            data=data,
            auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return resp.json().get('sid')
        _logger.warning('Twilio error %s: %s', resp.status_code, resp.json())
    except Exception as exc:
        _logger.error('Twilio request failed: %s', exc)
    return None


class WhatsAppDashboardController(http.Controller):

    # ── 1. Thread list ───────────────────────────────────────────────────────
    @http.route('/whatsapp_dashboard/threads', type='json', auth='user', methods=['POST'])
    def get_threads(self):
        """Return all threads ordered by last message date."""
        threads = request.env['whatsapp.thread'].search([])
        return {'threads': [t.get_thread_data() for t in threads]}

    # ── 2. Messages for one thread ───────────────────────────────────────────
    @http.route('/whatsapp_dashboard/messages', type='json', auth='user', methods=['POST'])
    def get_messages(self, thread_id, **kwargs):
        msgs = request.env['whatsapp.message'].search([
            ('thread_id', '=', int(thread_id))
        ])
        return {'messages': [m.get_message_data() for m in msgs]}

    # ── 3. Mark thread as read ───────────────────────────────────────────────
    @http.route('/whatsapp_dashboard/mark_read', type='json', auth='user', methods=['POST'])
    def mark_read(self, thread_id, **kwargs):
        env = request.env
        unread = env['whatsapp.message'].search([
            ('thread_id', '=', int(thread_id)),
            ('direction', '=', 'incoming'),
            ('status',    '!=', 'read'),
        ])
        unread.write({'status': 'read'})
        thread = env['whatsapp.thread'].browse(int(thread_id))
        if thread.exists():
            thread.unread_count = 0
        return {'success': True}

    # ── 4. Send message (saves locally + calls Twilio for external) ──────────
    @http.route('/whatsapp_dashboard/send_message', type='json', auth='user', methods=['POST'])
    def send_message(self, thread_id, body, msg_type, media_id=None, **kwargs):
        env = request.env
        thread = env['whatsapp.thread'].browse(int(thread_id))
        if not thread.exists():
            return {'error': 'Thread not found'}

        # Build media URL if media_id is provided
        media_url = None
        if media_id:
            attachment = env['ir.attachment'].browse(int(media_id))
            if attachment.exists() and attachment.public:
                base_url = env['ir.config_parameter'].sudo().get_param('web.base.url')
                media_url = f"{base_url}/web/content/{attachment.id}?download=true"

        # Call Twilio for external messages
        twilio_sid = None
        if msg_type == 'external' and thread.phone:
            twilio_sid = _send_via_twilio(thread.phone, body, media_url)

        # Create local message record
        msg_vals = {
            'thread_id':    thread.id,
            'body':         body or '📎 Media message',
            'direction':    'outgoing',
            'message_type': msg_type,
            'status':       'sent',
            'timestamp':    fields.Datetime.now(),
            'twilio_sid':   twilio_sid,
        }
        if media_id:
            msg_vals['attachment_id'] = int(media_id)

        msg = env['whatsapp.message'].create(msg_vals)

        thread.write({
            'last_message':      body[:200] or '📎 Media message',
            'last_message_date': fields.Datetime.now(),
        })

        return {
            'success':      True,
            'message_id':   msg.id,
            'message_data': msg.get_message_data(),
            'twilio_sid':   twilio_sid,
        }

    # ── 5. Poll for new messages (lightweight real-time) ─────────────────────
    @http.route('/whatsapp_dashboard/poll', type='json', auth='user', methods=['POST'])
    def poll(self, thread_id, last_message_id, **kwargs):
        """Return messages newer than last_message_id plus updated thread list."""
        new_msgs = request.env['whatsapp.message'].search([
            ('thread_id', '=', int(thread_id)),
            ('id',        '>',  int(last_message_id)),
        ])
        threads = request.env['whatsapp.thread'].search([])
        return {
            'new_messages': [m.get_message_data() for m in new_msgs],
            'threads':      [t.get_thread_data()  for t in threads],
        }

    # ── 6. Twilio inbound webhook ─────────────────────────────────────────────
    @http.route(
        '/whatsapp/webhook/inbound',
        type='http', auth='public', methods=['POST'], csrf=False,
    )
    def twilio_inbound(self, **post):
        """Twilio POSTs here when someone messages the sandbox number."""
        from_raw = post.get('From', '')          # "whatsapp:+9779819059190"
        body     = post.get('Body', '').strip()
        sid      = post.get('MessageSid', '')
        phone    = from_raw.replace('whatsapp:', '').strip()

        if not phone or not body:
            return request.make_response('OK', [('Content-Type', 'text/plain')])

        env = request.env['whatsapp.thread'].sudo()

        # Deduplicate by Twilio SID
        if sid and request.env['whatsapp.message'].sudo().search(
            [('twilio_sid', '=', sid)], limit=1
        ):
            return request.make_response('OK', [('Content-Type', 'text/plain')])

        thread = env.search([('phone', '=', phone)], limit=1)

        if not thread:
            thread = env.create({
                'name':              phone,
                'phone':             phone,
                'avatar_color':      '#25D366',
                'status':            'online',
                'thread_type':       'external',
                'last_message':      body[:200],
                'last_message_date': fields.Datetime.now(),
                'unread_count':      1,
            })
        else:
            thread.write({
                'last_message':      body[:200],
                'last_message_date': fields.Datetime.now(),
                'unread_count':      thread.unread_count + 1,
                'status':            'online',
            })

        request.env['whatsapp.message'].sudo().create({
            'thread_id':    thread.id,
            'body':         body,
            'direction':    'incoming',
            'message_type': 'external',
            'status':       'delivered',
            'timestamp':    fields.Datetime.now(),
            'twilio_sid':   sid,
        })

        twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
        return request.make_response(twiml, [('Content-Type', 'text/xml')])

    # ── 7. Upload media (for attachments) ─────────────────────────────────────
    @http.route('/whatsapp_dashboard/upload_media', type='http', auth='user', methods=['POST'], csrf=False)
    def upload_media(self):
        """Receive a file, save as ir.attachment, return attachment ID and public URL."""
        file = request.httprequest.files.get('file')
        if not file:
            return request.make_response(
                json.dumps({'error': 'No file provided'}),
                status=400,
                headers=[('Content-Type', 'application/json')]
            )

        file_data = file.read()
        # Twilio WhatsApp media limit is 5 MB
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
            'public': True,   # required for Twilio to access
        })

        base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
        media_url = f"{base_url}/web/content/{attachment.id}?download=true"

        return request.make_response(
            json.dumps({
                'attachment_id': attachment.id,
                'media_url': media_url,
            }),
            headers=[('Content-Type', 'application/json')]
        )

    # ── 8. Fetch available phone numbers from Twilio (with fallback) ──────────
    @http.route('/whatsapp_dashboard/available_numbers', type='jsonrpc', auth='user', methods=['POST'])
    def available_numbers(self, country_code='US', number_type='local', **kwargs):
        """
        Fetch available phone numbers from Twilio for a given country.
        If the API fails (e.g., 404), returns fallback demo numbers.
        """
        # Fallback numbers (used if API fails)
        fallback = [
            {'id': '1', 'number': '+1 555-0101', 'type': 'Mobile', 'capabilities': 'SMS, Voice, WhatsApp', 'monthlyCost': '5.00', 'setupFee': '0.00'},
            {'id': '2', 'number': '+1 555-0102', 'type': 'Mobile', 'capabilities': 'SMS, Voice, WhatsApp', 'monthlyCost': '5.00', 'setupFee': '0.00'},
            {'id': '3', 'number': '+1 555-0103', 'type': 'Mobile', 'capabilities': 'SMS, Voice', 'monthlyCost': '3.50', 'setupFee': '0.00'},
            {'id': '4', 'number': '+1 555-0104', 'type': 'Mobile', 'capabilities': 'SMS, Voice, WhatsApp', 'monthlyCost': '5.00', 'setupFee': '0.00'},
            {'id': '5', 'number': '+1 555-0105', 'type': 'Mobile', 'capabilities': 'SMS, Voice', 'monthlyCost': '3.50', 'setupFee': '0.00'},
            {'id': '6', 'number': '+1 555-0106', 'type': 'Mobile', 'capabilities': 'SMS, Voice, WhatsApp', 'monthlyCost': '5.00', 'setupFee': '0.00'},
        ]

        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            return {'numbers': fallback, 'error': 'Credentials not configured'}

        # Build the API URL
        api_url = f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/AvailablePhoneNumbers/{country_code}/{number_type.capitalize()}.json'

        try:
            resp = requests.get(
                api_url,
                auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                timeout=10,
                params={'Limit': 20}
            )
            if resp.status_code == 200:
                data = resp.json()
                numbers = []
                for num in data.get('available_phone_numbers', []):
                    caps = [k.title() for k, v in num.get('capabilities', {}).items() if v]
                    numbers.append({
                        'id': num.get('friendly_name'),
                        'number': num.get('friendly_name'),
                        'type': number_type.capitalize(),
                        'capabilities': ', '.join(caps),
                        'monthlyCost': str(num.get('monthly_price', '0.00')),
                        'setupFee': str(num.get('setup_fee', '0.00')),
                    })
                return {'numbers': numbers}
            else:
                # If API fails, return fallback numbers
                return {'numbers': fallback}
        except Exception as e:
            # On any error, return fallback
            return {'numbers': fallback}