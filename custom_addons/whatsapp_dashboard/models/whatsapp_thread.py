from odoo import models, fields, api
from datetime import datetime, timezone

class WhatsAppThread(models.Model):
    _name = 'whatsapp.thread'
    _description = 'WhatsApp Conversation Thread'
    _order = 'last_message_date desc, id desc'

    name = fields.Char('Contact Name', required=True)
    initials = fields.Char('Initials', compute='_compute_initials', store=True)
    avatar_color = fields.Char('Avatar Color', default='#25D366')
    phone = fields.Char('Phone Number')
    partner_id = fields.Many2one('res.partner', string='Customer', ondelete='set null')
    last_message = fields.Char('Last Message Preview')
    last_message_date = fields.Datetime('Last Message Date', default=fields.Datetime.now)
    unread_count = fields.Integer('Unread Count', default=0)
    status = fields.Selection([('online', 'Online'), ('offline', 'Offline')], default='offline')
    thread_type = fields.Selection([('external', 'External'), ('internal', 'Internal Notes')], default='external')
    message_ids = fields.One2many('whatsapp.message', 'thread_id', string='Messages')
    active = fields.Boolean(default=True)

    @api.depends('name')
    def _compute_initials(self):
        for rec in self:
            parts = (rec.name or '').split()
            if len(parts) >= 2:
                rec.initials = (parts[0][0] + parts[1][0]).upper()
            elif parts:
                rec.initials = parts[0][:2].upper()
            else:
                rec.initials = 'XX'

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            self.name = self.partner_id.name
            # Safely get phone/mobile (Odoo 19 uses 'mobile' field, but fallback to empty)
            self.phone = self.partner_id.phone or getattr(self.partner_id, 'mobile', '') or ''

    def _format_time_display(self):
        if not self.last_message_date:
            return ''
        now = datetime.now(timezone.utc)
        msg_dt = self.last_message_date.replace(tzinfo=timezone.utc)
        delta = now - msg_dt
        if delta.days == 0:
            return msg_dt.strftime('%I:%M %p').lstrip('0')
        elif delta.days == 1:
            return 'Yesterday'
        elif delta.days < 7:
            return msg_dt.strftime('%A')
        else:
            return msg_dt.strftime('%m/%d/%Y')

    def get_thread_data(self):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'initials': self.initials or 'XX',
            'color': self.avatar_color or '#25D366',
            'phone': self.phone or '',
            'last_message': self.last_message or '',
            'time': self._format_time_display(),
            'unread': self.unread_count,
            'status': self.status,
            'type': self.thread_type,
            'partner_id': self.partner_id.id if self.partner_id else False,
        }

    def action_sync_customers(self):
        """Fetch all customers from res.partner and create/update threads."""
        partners = self.env['res.partner'].search([('customer_rank', '>', 0)])
        created = 0
        updated = 0
        for partner in partners:
            # Safely get phone/mobile
            phone = partner.phone or getattr(partner, 'mobile', '') or ''
            if not phone:
                continue
            thread = self.search([('phone', '=', phone)], limit=1)
            if thread:
                thread.write({
                    'name': partner.name,
                    'partner_id': partner.id,
                })
                updated += 1
            else:
                self.create({
                    'name': partner.name,
                    'phone': phone,
                    'partner_id': partner.id,
                    'avatar_color': '#25D366',
                    'status': 'offline',
                    'thread_type': 'external',
                    'last_message': 'Synced from customers',
                    'last_message_date': fields.Datetime.now(),
                })
                created += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync Complete',
                'message': f'{created} new threads created, {updated} updated.',
                'sticky': False,
            }
        }