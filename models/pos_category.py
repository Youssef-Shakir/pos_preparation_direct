from odoo import models, fields


class PosCategory(models.Model):
    _inherit = 'pos.category'

    preparation_printer_ids = fields.Many2many(
        'pos.preparation.printer',
        'pos_category_preparation_printer_rel',
        'category_id',
        'printer_id',
        string='Preparation Printers',
        help='Printers that will receive preparation tickets for products in this category'
    )
