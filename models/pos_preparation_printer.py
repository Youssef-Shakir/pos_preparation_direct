import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from . import escpos_encoder

_logger = logging.getLogger(__name__)


class PosPreparationPrinter(models.Model):
    _name = 'pos.preparation.printer'
    _description = 'POS Preparation Printer'

    name = fields.Char(string='Name', required=True)
    printer_type = fields.Selection([
        ('kitchen', 'Kitchen'),
        ('bar', 'Bar'),
        ('prep', 'Prep Station'),
        ('other', 'Other'),
    ], string='Type', default='kitchen', required=True)

    # Network settings
    ip_address = fields.Char(string='IP Address', required=True)
    port = fields.Integer(string='Port', default=9100, required=True)

    # Printer settings
    codepage = fields.Selection([
        ('cp437', 'PC437 (US)'),
        ('cp850', 'PC850 (Western European)'),
        ('cp860', 'PC860 (Portuguese)'),
        ('cp1252', 'Windows-1252'),
        ('cp864', 'PC864 (Arabic DOS)'),
        ('cp1256', 'Windows-1256 (Arabic)'),
        ('utf8', 'UTF-8 (Modern Printers)'),
    ], string='Codepage', default='cp437', required=True)
    paper_width = fields.Selection([
        ('32', '58mm (32 chars)'),
        ('42', '80mm (42 chars)'),
        ('48', '80mm (48 chars)'),
    ], string='Paper Width', default='42', required=True)
    auto_cut = fields.Boolean(string='Auto Cut', default=True)
    beep = fields.Boolean(string='Beep on Print', default=True)
    footer_text = fields.Char(string='Footer Text', help='Custom text to print at the bottom of tickets')

    # Retry settings
    retry_count = fields.Integer(
        string='Retry Attempts',
        default=3,
        help='Number of times to retry if printer is offline'
    )
    retry_interval = fields.Integer(
        string='Retry Interval (seconds)',
        default=30,
        help='Seconds to wait between retry attempts'
    )
    connection_timeout = fields.Integer(
        string='Connection Timeout (seconds)',
        default=5,
        help='Seconds to wait for printer connection'
    )

    # Relations
    pos_config_ids = fields.Many2many(
        'pos.config',
        'pos_config_preparation_printer_rel',
        'printer_id',
        'config_id',
        string='POS Configurations'
    )
    pos_category_ids = fields.Many2many(
        'pos.category',
        'pos_category_preparation_printer_rel',
        'printer_id',
        'category_id',
        string='Product Categories'
    )

    # Job tracking
    job_ids = fields.One2many('pos.preparation.job', 'printer_id', string='Print Jobs')
    job_count = fields.Integer(string='Job Count', compute='_compute_job_count')

    active = fields.Boolean(default=True)

    @api.depends('job_ids')
    def _compute_job_count(self):
        for printer in self:
            printer.job_count = len(printer.job_ids)

    def _get_printer_settings(self):
        """Return printer settings as a dictionary."""
        self.ensure_one()
        return {
            'codepage': self.codepage,
            'paper_width': int(self.paper_width),
            'auto_cut': self.auto_cut,
            'beep': self.beep,
            'footer_text': self.footer_text or '',
        }

    def action_test_connection(self):
        """Test the printer connection."""
        self.ensure_one()

        # Build a test ticket
        test_data = {
            'table_name': 'TEST',
            'floor_name': '',
            'waiter_name': 'System',
            'order_name': 'TEST-001',
            'customer_note': '',
            'lines': [
                {'product_name': 'Test Print', 'qty': 1, 'note': ''},
            ],
        }

        ticket_bytes = escpos_encoder.build_preparation_ticket(
            test_data,
            self._get_printer_settings()
        )

        success, error = escpos_encoder.send_to_printer(
            self.ip_address,
            self.port,
            ticket_bytes
        )

        if success:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Test print sent successfully!'),
                    'type': 'success',
                }
            }
        else:
            raise UserError(_('Print failed: %s') % error)

    def print_ticket(self, data, use_retry=True):
        """
        Print a preparation ticket with retry support.

        Args:
            data: dict with table_name, floor_name, waiter_name, order_name, customer_note, lines
            use_retry: bool, whether to use retry logic (default True)

        Returns:
            tuple: (success: bool, error_message: str or None)
        """
        self.ensure_one()
        import time

        ticket_bytes = escpos_encoder.build_preparation_ticket(
            data,
            self._get_printer_settings()
        )

        max_attempts = self.retry_count + 1 if use_retry else 1
        retry_interval = self.retry_interval
        timeout = self.connection_timeout

        last_error = None

        for attempt in range(max_attempts):
            if attempt > 0:
                _logger.info(f"Retry attempt {attempt}/{self.retry_count} for {self.name} in {retry_interval}s...")
                time.sleep(retry_interval)

            success, error = escpos_encoder.send_to_printer(
                self.ip_address,
                self.port,
                ticket_bytes,
                timeout=timeout
            )

            if success:
                _logger.info(f"Successfully printed to {self.name} ({self.ip_address}:{self.port})")
                return True, None

            last_error = error
            _logger.warning(f"Print attempt {attempt + 1}/{max_attempts} failed for {self.name}: {error}")

        _logger.error(f"All print attempts failed for {self.name}: {last_error}")
        return False, last_error

    def action_view_jobs(self):
        """View all jobs for this printer."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Print Jobs'),
            'res_model': 'pos.preparation.job',
            'view_mode': 'list,form',
            'domain': [('printer_id', '=', self.id)],
            'context': {'default_printer_id': self.id},
        }


class PosPreparationJob(models.Model):
    _name = 'pos.preparation.job'
    _description = 'POS Preparation Print Job'
    _order = 'create_date desc'

    printer_id = fields.Many2one('pos.preparation.printer', string='Printer', required=True, ondelete='cascade')
    order_id = fields.Many2one('pos.order', string='POS Order', ondelete='set null')
    order_name = fields.Char(string='Order Reference')
    order_uuid = fields.Char(string='Order UUID', index=True)

    state = fields.Selection([
        ('pending', 'Pending'),
        ('printed', 'Printed'),
        ('failed', 'Failed'),
    ], string='Status', default='pending', required=True)

    line_data = fields.Json(string='Line Data')
    table_name = fields.Char(string='Table')
    floor_name = fields.Char(string='Floor')
    waiter_name = fields.Char(string='Waiter')
    customer_note = fields.Text(string='Customer Note')

    printed_date = fields.Datetime(string='Printed Date')
    error_message = fields.Text(string='Error Message')
    retry_count = fields.Integer(string='Retry Count', default=0)

    line_summary = fields.Char(string='Summary', compute='_compute_line_summary')

    @api.depends('line_data')
    def _compute_line_summary(self):
        for job in self:
            if job.line_data:
                lines = job.line_data
                summary_parts = [f"{int(l.get('qty', 1))}x {l.get('product_name', '')[:15]}" for l in lines[:3]]
                job.line_summary = ', '.join(summary_parts)
                if len(lines) > 3:
                    job.line_summary += f" +{len(lines) - 3} more"
            else:
                job.line_summary = ''

    def action_retry(self):
        """Retry printing a failed job."""
        self.ensure_one()
        if self.state != 'failed':
            return

        data = {
            'table_name': self.table_name or '',
            'floor_name': self.floor_name or '',
            'waiter_name': self.waiter_name or '',
            'order_name': self.order_name or '',
            'customer_note': self.customer_note or '',
            'lines': self.line_data or [],
        }

        success, error = self.printer_id.print_ticket(data)

        self.retry_count += 1
        if success:
            self.state = 'printed'
            self.printed_date = fields.Datetime.now()
            self.error_message = False
        else:
            self.error_message = error

        return True

    @api.model
    def cleanup_old_jobs(self, days=7):
        """Clean up old printed jobs. Called by cron."""
        from datetime import timedelta
        cutoff = fields.Datetime.now() - timedelta(days=days)
        old_jobs = self.search([
            ('state', '=', 'printed'),
            ('printed_date', '<', cutoff)
        ])
        old_jobs.unlink()
        _logger.info(f"Cleaned up {len(old_jobs)} old preparation jobs")
