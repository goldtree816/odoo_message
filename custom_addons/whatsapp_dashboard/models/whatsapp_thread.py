from odoo import models, fields, api
from datetime import datetime, timezone


class WhatsAppThread(models.Model):
    _name = 'whatsapp.thread'
    _description = 'WhatsApp Conversation Thread'
    _order = 'last_message_date desc, id desc'

    # ── Fields ─────────────────────────────────────────────────────────────
    name             = fields.Char('Contact Name', required=True)
    initials         = fields.Char('Initials', compute='_compute_initials', store=True)
    avatar_color     = fields.Char('Avatar Color', default='#25D366')
    phone            = fields.Char('Phone Number')
    last_message     = fields.Char('Last Message Preview')
    last_message_date = fields.Datetime('Last Message Date', default=fields.Datetime.now)
    unread_count     = fields.Integer('Unread Count', default=0)
    status           = fields.Selection([
        ('online',  'Online'),
        ('offline', 'Offline'),
    ], default='offline', string='Online Status')
    thread_type      = fields.Selection([
        ('external', 'External'),
        ('internal', 'Internal Notes'),
    ], default='external', string='Thread Type')
    message_ids      = fields.One2many('whatsapp.message', 'thread_id', string='Messages')
    active           = fields.Boolean(default=True)

    # ── Computed ────────────────────────────────────────────────────────────
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

    # ── Helpers ─────────────────────────────────────────────────────────────
    def _format_time_display(self):
        """Human-readable label: HH:MM AM/PM, Yesterday, weekday, or date."""
        if not self.last_message_date:
            return ''
        now    = datetime.now(timezone.utc)
        msg_dt = self.last_message_date.replace(tzinfo=timezone.utc)
        delta  = now - msg_dt
        if delta.days == 0:
            return msg_dt.strftime('%I:%M %p').lstrip('0')
        elif delta.days == 1:
            return 'Yesterday'
        elif delta.days < 7:
            return msg_dt.strftime('%A')
        else:
            return msg_dt.strftime('%m/%d/%Y')

    def get_thread_data(self):
        """Dict suitable for JSON serialisation to the OWL frontend."""
        self.ensure_one()
        return {
            'id':           self.id,
            'name':         self.name,
            'initials':     self.initials or 'XX',
            'color':        self.avatar_color or '#25D366',
            'phone':        self.phone or '',
            'last_message': self.last_message or '',
            'time':         self._format_time_display(),
            'unread':       self.unread_count,
            'status':       self.status,
            'type':         self.thread_type,
        }