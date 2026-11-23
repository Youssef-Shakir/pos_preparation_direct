# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFailureAlert(TransactionCase):
    """A printer that does not print: an organized alert in the DoniaLink App, even without a Telegram bot."""

    def test_offline_printer_in_the_app(self):
        if 'donialink.app.message' not in self.env or not self.env.ref(
                'telegram_reports.kind_printer_alert', raise_if_not_found=False):
            self.skipTest('the DoniaLink App alerts (telegram_reports) are not installed')
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Prep Manager', 'login': 'prep_manager', 'groups_id': [(6, 0, self.env.ref('base.group_user').ids)]})
        self.env.ref('telegram_reports.kind_printer_alert').recipient_ids = user
        self.env.company.sudo().write({'telegram_bot_token': False})
        printer = self.env['pos.preparation.printer'].create({'name': 'Kitchen', 'ip_address': '10.0.0.9', 'port': 9100})
        job = self.env['pos.preparation.job'].create({
            'printer_id': printer.id, 'order_name': 'Order 00012', 'table_name': '5',
            'line_data': [{'qty': 2, 'product_name': 'Shawarma'}, {'qty': -1, 'product_name': 'Fries'}]})
        job._send_failure_alert('[Errno 113] No route to host')
        message = self.env['donialink.app.message'].search([('user_id', '=', user.id)])
        self.assertEqual(len(message), 1)
        self.assertEqual(message.kind_code, 'printer_alert')
        self.assertEqual(message.title, '📵 الطابعة غير متصلة: Kitchen')
        for piece in ('10.0.0.9:9100', 'Order 00012', 'Shawarma', 'No route to host', 'dl-rep-table'):
            self.assertIn(piece, str(message.body), piece)
