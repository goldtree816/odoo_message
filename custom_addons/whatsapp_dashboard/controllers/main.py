import logging
import json
import base64
import requests
from requests.auth import HTTPBasicAuth

from odoo import http, fields, tools
from odoo.http import request

_logger = logging.getLogger(__name__)


# ── Read Twilio credentials from odoo.conf ──────────────────────────────────
def _get_twilio_config():
    """Read Twilio credentials from Odoo configuration file.
       Add these to your odoo.conf:
         twilio_account_sid = AC...
         twilio_auth_token  = ...
         twilio_from        = whatsapp:+14155238886
    """
    return {
        'account_sid': tools.config.get('twilio_account_sid'),
        'auth_token':  tools.config.get('twilio_auth_token'),
        'from':        tools.config.get('twilio_from', 'whatsapp:+14155238886'),
    }


def _send_via_twilio(to_phone, body, media_url=None):
    """
    POST an outgoing WhatsApp message through the Twilio sandbox.
    If media_url is provided, it sends a media message with optional body.
    """
    config = _get_twilio_config()
    if not config['account_sid'] or not config['auth_token']:
        _logger.error('Twilio credentials not configured in odoo.conf')
        return None

    to_wa = f'whatsapp:{to_phone}' if not to_phone.startswith('whatsapp:') else to_phone
    api_url = (
        f'https://api.twilio.com/2010-04-01/Accounts/'
        f'{config["account_sid"]}/Messages.json'
    )
    data = {
        'From': config['from'],
        'To': to_wa,
        'Body': body,
    }
    if media_url:
        data['MediaUrl'] = media_url

    try:
        resp = requests.post(
            api_url,
            data=data,
            auth=HTTPBasicAuth(config['account_sid'], config['auth_token']),
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