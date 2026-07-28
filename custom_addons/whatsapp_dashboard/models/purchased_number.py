from odoo import models, fields


class PurchasedNumber(models.Model):
    # Stores the WhatsApp-capable (Twilio) phone numbers purchased/activated
    # for this Odoo database.
    _name = 'whatsapp.purchased_number'
    _description = 'Purchased WhatsApp Numbers'


    number = fields.Char('Phone Number', required=True)
    sid = fields.Char('Twilio SID')
    friendly_name = fields.Char('Friendly Name')
    status = fields.Selection([
        ('active', 'Active'),
        ('pending', 'Pending'),
        ('failed', 'Failed'),
    ], default='pending')
    purchase_date = fields.Datetime(default=fields.Datetime.now)

    is_sending_number = fields.Boolean(
        'Active for Sending',
        default=False,
        help='Only one number can be active for sending at a time.',
    )

    # NEW: Link to the subaccount that owns this number
    subaccount_id = fields.Many2one('whatsapp.subaccount', string='Subaccount')

