from odoo import models, fields


class PosConfig(models.Model):
    _inherit = 'pos.config'

    use_preparation_printing = fields.Boolean(
        string='Use Preparation Printing',
        help='Enable printing of preparation tickets to kitchen/bar printers'
    )
    preparation_printer_ids = fields.Many2many(
        'pos.preparation.printer',
        'pos_config_preparation_printer_rel',
        'config_id',
        'printer_id',
        string='Preparation Printers',
        help='Printers available for this POS configuration'
    )
