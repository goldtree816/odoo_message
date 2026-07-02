# importing main
from . import main
import logging
import json
import base64
import requests
from requests.auth import HTTPBasicAuth
from odoo import http, fields, _
from odoo.http import request

_logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
# Twilio credentials  (move these to Odoo config params
# or environment variables in production)
# ─────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = 'AC5b39938c26320f5d6207df9b59e5d345'
TWILIO_AUTH_TOKEN  = 'f7c9a8a481853993b532b1b65646273e'
TWILIO_FROM = 'whatsapp:+14155238886'
TWILIO_API_URL = (
    f'https://api.twilio.com/2010-04-01/Accounts/'
    f'{TWILIO_ACCOUNT_SID}/Messages.json'
)

# ─────────────────────────────────────────────────────
# Twilio subaccount helpers
# ─────────────────────────────────────────────────────

def _create_twilio_subaccount(friendly_name):
    """
    Call Twilio API to create a real subaccount.
    Returns (dict, None) on success  or  (None, error_string) on failure.
    """
    url = f'https://api.twilio.com/2010-04-01/Accounts.json'
    try:
        resp = requests.post(
            url,
            data={'FriendlyName': friendly_name},
            auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=15,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            _logger.info('Twilio subaccount created: %s', data.get('sid'))
            return {
                'sid':         data.get('sid', ''),
                'auth_token':  data.get('auth_token', ''),
                'friendly_name': data.get('friendly_name', friendly_name),
                'status':      data.get('status', 'active'),
                'date_created': data.get('date_created', ''),
            }, None
        # Twilio returned an error
        error_msg = f'Twilio API error {resp.status_code}'
        try:
            err_data = resp.json()
            error_msg += f": {err_data.get('message', resp.text[:200])}"
        except Exception:
            error_msg += f": {resp.text[:200]}"
        _logger.error('Twilio create subaccount failed: %s', error_msg)
        return None, error_msg
    except requests.exceptions.ConnectionError:
        error_msg = 'Cannot connect to Twilio API. Check your internet connection.'
        _logger.error(error_msg)
        return None, error_msg
    except requests.exceptions.Timeout:
        error_msg = 'Twilio API request timed out.'
        _logger.error(error_msg)
        return None, error_msg
    except Exception as exc:
        error_msg = f'Twilio request failed: {exc}'
        _logger.error(error_msg)
        return None, error_msg


def _close_twilio_subaccount(subaccount_sid):
    """
    Close (permanently delete) a subaccount on Twilio.
    Returns True on success, False on failure.
    """
    url = f'https://api.twilio.com/2010-04-01/Accounts/{subaccount_sid}.json'
    try:
        resp = requests.post(
            url,
            data={'Status': 'closed'},
            auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=15,
        )
        if resp.status_code in (200, 201):
            _logger.info('Twilio subaccount %s closed', subaccount_sid)
            return True
        _logger.error('Twilio close subaccount error %s: %s',
                       resp.status_code, resp.text)
        return False
    except Exception as exc:
        _logger.error('Twilio close subaccount request failed: %s', exc)
        return False


def _update_twilio_subaccount_status(subaccount_sid, new_status):
    """
    Suspend or activate a subaccount on Twilio.
    new_status: 'suspended' or 'active'
    Returns True on success, False on failure.
    """
    url = f'https://api.twilio.com/2010-04-01/Accounts/{subaccount_sid}.json'
    try:
        resp = requests.post(
            url,
            data={'Status': new_status},
            auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=15,
        )
        if resp.status_code in (200, 201):
            _logger.info('Twilio subaccount %s set to %s', subaccount_sid, new_status)
            return True
        _logger.error('Twilio update status error %s: %s',
                       resp.status_code, resp.text)
        return False
    except Exception as exc:
        _logger.error('Twilio update status request failed: %s', exc)
        return False


# ─────────────────────────────────────────────────────
# Existing WhatsApp send helper  (unchanged)
# ─────────────────────────────────────────────────────

def _send_via_twilio(to_phone, body, media_url=None):
    """POST an outgoing WhatsApp message through the Twilio sandbox."""
    to_wa = f'whatsapp:{to_phone}' if not to_phone.startswith('whatsapp:') else to_phone
    data = {
        'From': TWILIO_FROM,
        'To':   to_wa,
        'Body': body,
    }
    if media_url:
        # Twilio WhatsApp media uses MediaUrl0/MediaUrl1/... + NumMedia
        data['NumMedia'] = 1
        data['MediaUrl0'] = media_url

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

    # ── 1. Thread list ──────────────────────────────────────────────────
    @http.route('/whatsapp_dashboard/threads', type='json', auth='user', methods=['POST'])
    def get_threads(self):
        threads = request.env['whatsapp.thread'].search([])
        return {'threads': [t.get_thread_data() for t in threads]}

    # ── 2. Messages for one thread ──────────────────────────────────────
    @http.route('/whatsapp_dashboard/messages', type='json', auth='user', methods=['POST'])
    def get_messages(self, thread_id, **kwargs):
        msgs = request.env['whatsapp.message'].search([
            ('thread_id', '=', int(thread_id))
        ])
        return {'messages': [m.get_message_data() for m in msgs]}

    # ── 3. Mark thread as read ──────────────────────────────────────────
    @http.route('/whatsapp_dashboard/mark_read', type='json', auth='user', methods=['POST'])
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

    # ── 4. Send message (saves locally + calls Twilio) ──────────────────
    @http.route('/whatsapp_dashboard/send_message', type='json', auth='user', methods=['POST'])
    def send_message(self, thread_id, body, msg_type, media_id=None, **kwargs):
        env = request.env
        thread = env['whatsapp.thread'].browse(int(thread_id))
        if not thread.exists():
            return {'error': 'Thread not found'}

        # Build media URL if media_id is provided
        # Media sending fix: Twilio must be able to fetch the URL.
        # If the attachment isn't public yet, make it public temporarily.
        media_url = None
        if media_id:
            attachment = env['ir.attachment'].sudo().browse(int(media_id))
            if attachment.exists():
                if not attachment.public:
                    attachment.public = True
                base_url = env['ir.config_parameter'].sudo().get_param('web.base.url')
                media_url = f"{base_url}/web/content/{attachment.id}?download=true"

        # Call Twilio for external messages
        twilio_sid = None
        if msg_type == 'external' and thread.phone:
            twilio_sid = _send_via_twilio(thread.phone, body, media_url)

        # Create local message record
        msg_vals = {
            'thread_id': thread.id,
            'body': body or 'Media message',
            'direction': 'outgoing',
            'message_type': msg_type,
            'status': 'sent',
            'timestamp': fields.Datetime.now(),
            'twilio_sid': twilio_sid,
        }
        if media_id:
            msg_vals['attachment_id'] = int(media_id)

        msg = env['whatsapp.message'].create(msg_vals)
        thread.write({
            'last_message': body[:200] or 'Media message',
            'last_message_date': fields.Datetime.now(),
        })
        return {
            'success': True,
            'message_id': msg.id,
            'message_data': msg.get_message_data(),
            'twilio_sid': twilio_sid,
        }

    # ── 5. Poll for new messages ────────────────────────────────────────
    @http.route('/whatsapp_dashboard/poll', type='json', auth='user', methods=['POST'])
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

    # ── 6. Twilio inbound webhook ───────────────────────────────────────
    @http.route(
        '/whatsapp/webhook/inbound',
        type='http', auth='public', methods=['POST'], csrf=False,
    )
    def twilio_inbound(self, **post):
        from_raw = post.get('From', '')
        body = post.get('Body', '').strip()
        sid  = post.get('MessageSid', '')
        phone = from_raw.replace('whatsapp:', '').strip()
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
                'name': phone,
                'phone': phone,
                'avatar_color': '#25D366',
                'status': 'online',
                'thread_type': 'external',
                'last_message': body[:200],
                'last_message_date': fields.Datetime.now(),
                'unread_count': 1,
            })
        else:
            thread.write({
                'last_message': body[:200],
                'last_message_date': fields.Datetime.now(),
                'unread_count': thread.unread_count + 1,
                'status': 'online',
            })

        request.env['whatsapp.message'].sudo().create({
            'thread_id': thread.id,
            'body': body,
            'direction': 'incoming',
            'message_type': 'external',
            'status': 'delivered',
            'timestamp': fields.Datetime.now(),
            'twilio_sid': sid,
        })

        twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
        return request.make_response(twiml, [('Content-Type', 'text/xml')])

    # ── 7. Upload media ─────────────────────────────────────────────────
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
            'public': True,
        })
        base_url = env['ir.config_parameter'].sudo().get_param('web.base.url')
        media_url = f"{base_url}/web/content/{attachment.id}?download=true"
        return request.make_response(
            json.dumps({
                'attachment_id': attachment.id,
                'media_url': media_url,
            }),
            headers=[('Content-Type', 'application/json')]
        )

    # ── 8. Fetch available phone numbers ────────────────────────────────
    @http.route('/whatsapp_dashboard/available_numbers', type='jsonrpc', auth='user',
                methods=['POST'])
    def available_numbers(self, country_code='US', number_type='local', **kwargs):
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            return {'error': 'Twilio credentials not configured'}
        api_url = (
            f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}'
            f'/AvailablePhoneNumbers/{country_code}/{number_type.capitalize()}.json'
        )
        try:
            resp = requests.get(
                api_url,
                auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                timeout=10,
                params={'Limit': 20}
            )
            data = resp.json()
            if resp.status_code == 200:
                numbers = []
                for num in data.get('available_phone_numbers', []):
                    caps = []
                    for k, v in num.get('capabilities', {}).items():
                        if v:
                            caps.append(k.title())
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
                _logger.warning('Twilio API error %s: %s', resp.status_code, data)
                return {'error': data.get('message', 'Unknown error')}
        except Exception as e:
            _logger.error('Failed to fetch available numbers: %s', e)
            return {'error': str(e)}

    # ═══════════════════════════════════════════════════════════════════
    # SUBACCOUNTS ROUTES  —  NOW WITH TWILIO API INTEGRATION
    # ═══════════════════════════════════════════════════════════════════

    @http.route('/whatsapp_dashboard/subaccounts', type='json', auth='user', methods=['POST'])
    def get_subaccounts(self, **kwargs):
        subaccounts = request.env['whatsapp.subaccount'].search([])
        return {
            'subaccounts': [s.get_subaccount_data() for s in subaccounts],
            'total': len(subaccounts),
            'total_phone_numbers': sum(s.phone_numbers_count for s in subaccounts),
            'total_sms_sent': sum(s.sms_sent_this_month for s in subaccounts),
            'total_voice_minutes': sum(s.voice_minutes_this_month for s in subaccounts),
        }

    @http.route('/whatsapp_dashboard/subaccount/create',
                type='json', auth='user', methods=['POST'])
    def create_subaccount(self, name, unique_name, email='', status='active',
                          subaccount_type='standard', voice=True, sms=True,
                          mms=True, whatsapp=True, **kwargs):
        env = request.env

        # ── Check duplicate unique_name in Odoo ──
        existing = env['whatsapp.subaccount'].search(
            [('unique_name', '=', unique_name)], limit=1
        )
        if existing:
            return {'error': 'Unique name already exists. Please choose another.'}

        # ══════════════════════════════════════════════════════════
        # STEP 1:  CREATE SUBACCOUNT ON TWILIO
        # ══════════════════════════════════════════════════════════
        twilio_result, twilio_error = _create_twilio_subaccount(friendly_name=name)

        if not twilio_result:
            return {'error': f'Twilio error: {twilio_error}'}

        # ══════════════════════════════════════════════════════════
        # STEP 2:  SAVE TO ODOO WITH REAL TWILIO SID & AUTH TOKEN
        # ══════════════════════════════════════════════════════════
        try:
            subaccount = env['whatsapp.subaccount'].create({
                'name': name,
                'unique_name': unique_name,
                'email': email,
                'status': status,
                'subaccount_type': subaccount_type,
                'voice_enabled': voice,
                'sms_enabled': sms,
                'mms_enabled': mms,
                'whatsapp_enabled': whatsapp,
                # Real Twilio credentials — saved from the API response
                'sid': twilio_result['sid'],
                'auth_token': twilio_result['auth_token'],
            })
            return {
                'success': True,
                'subaccount': subaccount.get_subaccount_data(),
                'twilio_sid': twilio_result['sid'],
            }
        except Exception as e:
            # Rollback: close the Twilio subaccount we just created
            _close_twilio_subaccount(twilio_result['sid'])
            return {'error': str(e)}

    @http.route('/whatsapp_dashboard/subaccount/update',
                type='json', auth='user', methods=['POST'])
    def update_subaccount(self, subaccount_id, **kwargs):
        env = request.env
        subaccount = env['whatsapp.subaccount'].browse(int(subaccount_id))
        if not subaccount.exists():
            return {'error': 'Subaccount not found'}

        update_vals = {}
        allowed_fields = [
            'name', 'unique_name', 'email', 'status', 'subaccount_type',
            'voice_enabled', 'sms_enabled', 'mms_enabled', 'whatsapp_enabled',
            'phone_numbers_count', 'sms_sent_this_month',
            'voice_minutes_this_month', 'usage_cost_this_month',
        ]
        for field in allowed_fields:
            if field in kwargs:
                update_vals[field] = kwargs[field]

        if 'unique_name' in update_vals:
            dup = env['whatsapp.subaccount'].search([
                ('unique_name', '=', update_vals['unique_name']),
                ('id', '!=', subaccount.id)
            ], limit=1)
            if dup:
                return {'error': 'Unique name already exists.'}

        try:
            subaccount.write(update_vals)
            return {
                'success': True,
                'subaccount': subaccount.get_subaccount_data()
            }
        except Exception as e:
            return {'error': str(e)}

    @http.route('/whatsapp_dashboard/subaccount/delete',
                type='json', auth='user', methods=['POST'])
    def delete_subaccount(self, subaccount_id, **kwargs):
        env = request.env
        subaccount = env['whatsapp.subaccount'].browse(int(subaccount_id))
        if not subaccount.exists():
            return {'error': 'Subaccount not found'}

        try:
            name = subaccount.name
            twilio_sid = subaccount.sid

            # ══════════════════════════════════════════════════════════
            # CLOSE SUBACCOUNT ON TWILIO (permanent)
            # ══════════════════════════════════════════════════════════
            twilio_deleted = False
            if twilio_sid:
                twilio_deleted = _close_twilio_subaccount(twilio_sid)

            # Delete from Odoo
            subaccount.unlink()

            message = f'Subaccount "{name}" deleted successfully'
            if twilio_sid and twilio_deleted:
                message += ' (also closed on Twilio)'
            elif twilio_sid and not twilio_deleted:
                message += ' (deleted from Odoo, but failed to close on Twilio)'

            return {'success': True, 'message': message}
        except Exception as e:
            return {'error': str(e)}

    @http.route('/whatsapp_dashboard/subaccount/toggle_status',
                type='json', auth='user', methods=['POST'])
    def toggle_subaccount_status(self, subaccount_id, **kwargs):
        env = request.env
        subaccount = env['whatsapp.subaccount'].browse(int(subaccount_id))
        if not subaccount.exists():
            return {'error': 'Subaccount not found'}

        new_status = 'suspended' if subaccount.status == 'active' else 'active'

        # ══════════════════════════════════════════════════════════
        # UPDATE STATUS ON TWILIO
        # ══════════════════════════════════════════════════════════
        twilio_sid = subaccount.sid
        twilio_updated = False
        if twilio_sid:
            twilio_updated = _update_twilio_subaccount_status(
                twilio_sid, new_status
            )

        # Update in Odoo regardless
        subaccount.write({'status': new_status})

        result = {
            'success': True,
            'status': new_status,
            'subaccount': subaccount.get_subaccount_data(),
        }

        if twilio_sid and not twilio_updated:
            result['warning'] = (
                f'Status updated in Odoo to {new_status}, '
                f'but failed to update on Twilio.'
            )

        return result