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
    # retry_count / retry_interval are used ONLY by the background cron job.
    # The HTTP request always makes exactly ONE attempt (no sleeping in workers).
    retry_count = fields.Integer(
        string='Auto-Retry Attempts (cron)',
        default=0,
        help='How many times the cron job will retry a failed print. '
             '0 = no automatic retry (retry manually from Print Jobs list).'
    )
    retry_interval = fields.Integer(
        string='Retry Interval (minutes)',
        default=5,
        help='Minutes between automatic retry attempts by the cron job.'
    )
    connection_timeout = fields.Integer(
        string='Connection Timeout (seconds)',
        default=5,
        help='Seconds to wait for printer connection before giving up.'
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
        # Convert current time to user's timezone for correct printing
        now_utc = fields.Datetime.now()
        now_local = fields.Datetime.context_timestamp(self, now_utc)
        print_time = now_local.strftime('%Y-%m-%d %H:%M')

        test_data = {
            'table_name': 'TEST',
            'floor_name': '',
            'waiter_name': 'System',
            'order_name': 'TEST-001',
            'customer_note': '',
            'lines': [
                {'product_name': 'Test Print', 'qty': 1, 'note': ''},
            ],
            'print_time': print_time,
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

    def print_ticket(self, data):
        """
        Send one preparation ticket to the printer — single attempt, no sleeping.

        Retries must NEVER happen inside an HTTP request (they block the Odoo
        worker thread and cause browser timeouts which trigger re-sends).
        Background retries are handled by the cron job via action_retry_failed_jobs.

        Returns:
            tuple: (success: bool, error_message: str or None)
        """
        self.ensure_one()
        ticket_bytes = escpos_encoder.build_preparation_ticket(
            data,
            self._get_printer_settings()
        )
        success, error = escpos_encoder.send_to_printer(
            self.ip_address,
            self.port,
            ticket_bytes,
            timeout=self.connection_timeout,
        )
        if success:
            _logger.info("Printed to %s (%s:%s)", self.name, self.ip_address, self.port)
        else:
            _logger.warning("Print failed on %s: %s", self.name, error)
        return success, error

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

    # One job per (request, printer) pair — allows multiple printers per request
    # while still blocking duplicate sends to the same printer.
    # NULL print_request_id is excluded from the constraint automatically (PostgreSQL).
    _sql_constraints = [
        ('print_request_printer_uniq', 'UNIQUE(print_request_id, printer_id)',
         'This print request has already been processed for this printer.'),
    ]

    printer_id = fields.Many2one('pos.preparation.printer', string='Printer', required=True, ondelete='cascade')
    order_id = fields.Many2one('pos.order', string='POS Order', ondelete='set null')
    order_name = fields.Char(string='Order Reference')
    order_uuid = fields.Char(string='Order UUID', index=True)

    # Idempotency key sent from the browser per print attempt
    print_request_id = fields.Char(string='Request ID', index=True)

    # Sequential ticket number for physical receipt identification
    ticket_number = fields.Integer(string='Ticket #', readonly=True, index=True)

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
    preview_text = fields.Text(string='Ticket Preview', compute='_compute_preview_text', store=False)

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

    @api.depends('line_data', 'order_name', 'table_name', 'floor_name',
                 'waiter_name', 'ticket_number', 'printed_date', 'customer_note')
    def _compute_preview_text(self):
        for job in self:
            parts = []
            if job.ticket_number:
                parts.append(f"=== TICKET #{job.ticket_number:04d} ===")
            if job.table_name:
                floor = f"{job.floor_name} - " if job.floor_name else ""
                parts.append(f"TABLE: {floor}{job.table_name}")
            if job.order_name:
                parts.append(f"Order: {job.order_name}")
            if job.waiter_name:
                parts.append(f"Waiter: {job.waiter_name}")
            if job.printed_date:
                # Convert to user's timezone for display
                local_time = fields.Datetime.context_timestamp(job, job.printed_date)
                parts.append(f"Printed: {local_time.strftime('%Y-%m-%d %H:%M:%S')}")
            parts.append("-" * 32)
            for item in (job.line_data or []):
                qty = item.get('qty', 1)
                name = item.get('product_name', '')
                note = item.get('note', '')
                if int(qty) < 0:
                    parts.append(f"  ** CANCEL {abs(int(qty))}x {name}")
                else:
                    parts.append(f"  {int(qty)}x {name}")
                if note:
                    parts.append(f"    >> {note}")
            if job.customer_note:
                parts.append("-" * 32)
                parts.append(f"NOTE: {job.customer_note}")
            job.preview_text = '\n'.join(parts)

    def _send_failure_alert(self, error_message=None):
        """
        Send a Telegram alert when this print job fails.
        No-ops silently if telegram_reports is not installed or not configured.
        """
        if 'telegram.service' not in self.env:
            return
        try:
            svc = self.env['telegram.service']
            token = svc._get_bot_token()
            chat_id = svc._get_default_chat_id()
            if not token or not chat_id:
                return

            printer = self.printer_id
            err = (error_message or self.error_message or 'Unknown error')[:200]

            is_offline = any(kw in err for kw in (
                'No route to host', 'Connection refused',
                'timed out', 'Network unreachable', 'Errno'))

            status = '📵 OFFLINE' if is_offline else '🔴 FAILED'

            lines_preview = ''
            for item in (self.line_data or [])[:5]:
                qty = item.get('qty', 1)
                name = item.get('product_name', '')
                lines_preview += f"\n  {'❌' if qty < 0 else '•'} {qty}x {name}"

            msg = (
                f"<b>{status} — Printer Alert</b>\n"
                f"🖨 <b>{printer.name}</b>  ({printer.ip_address}:{printer.port})\n"
                f"📋 Order: {self.order_name or '—'}"
            )
            if self.table_name:
                msg += f"   🪑 Table: {self.table_name}"
            if self.ticket_number:
                msg += f"\n🎫 Ticket #{self.ticket_number:04d}"
            msg += f"\n{lines_preview}"
            msg += f"\n\n⚠️ <code>{err}</code>"

            svc.send_message(msg, chat_id=chat_id)
        except Exception:
            _logger.exception("[PrepDirect] Failed to send Telegram alert for job #%s", self.id)

    def action_retry(self):
        """
        Retry a failed job — dedup-aware.

        Before reprinting, checks how much of each line_uuid was already
        printed by OTHER successful jobs for the same order. Only prints
        the remaining delta. This prevents double-prints when:
          - the normal POS flow already resent the order while this job was failing
          - the cron AND a manual retry fire at the same time
        """
        self.ensure_one()
        if self.state not in ('failed', 'pending'):
            return

        order_uuid = self.order_uuid or ''

        # Sum what other PRINTED jobs already covered for this order
        already_by_uuid = {}
        if order_uuid:
            other_jobs = self.search([
                ('order_uuid', '=', order_uuid),
                ('state', '=', 'printed'),
                ('id', '!=', self.id),
            ])
            for job in other_jobs:
                for item in (job.line_data or []):
                    luuid = item.get('line_uuid', '')
                    if luuid:
                        already_by_uuid[luuid] = (
                            already_by_uuid.get(luuid, 0) + item.get('qty', 0)
                        )

        # Compute delta lines — skip anything already covered
        lines_to_print = []
        for item in (self.line_data or []):
            luuid = item.get('line_uuid', '')
            stored_qty = item.get('qty', 0)
            already = already_by_uuid.get(luuid, 0)
            delta = stored_qty - already
            if delta == 0:
                _logger.info(
                    "[PrepDirect retry] %s line_uuid=%s — skipping (already printed by other job)",
                    item.get('product_name'), luuid)
                continue
            lines_to_print.append(dict(item, qty=delta))

        self.retry_count += 1

        if not lines_to_print:
            _logger.info("[PrepDirect retry] Job #%s — nothing left to print (all covered)", self.id)
            self.state = 'printed'
            self.printed_date = fields.Datetime.now()
            self.error_message = False
            return True

        # Convert current time to user's timezone for correct printing
        now_utc = fields.Datetime.now()
        now_local = fields.Datetime.context_timestamp(self, now_utc)
        print_time = now_local.strftime('%Y-%m-%d %H:%M')

        data = {
            'table_name':    self.table_name or '',
            'floor_name':    self.floor_name or '',
            'waiter_name':   self.waiter_name or '',
            'order_name':    self.order_name or '',
            'customer_note': self.customer_note or '',
            'lines':         lines_to_print,
            'ticket_number': self.ticket_number or 0,
            'print_time':    print_time,
        }

        success, error = self.printer_id.print_ticket(data)

        if success:
            self.state = 'printed'
            self.printed_date = fields.Datetime.now()
            self.error_message = False
            _logger.info("[PrepDirect retry] Job #%s — printed OK on retry #%s",
                         self.id, self.retry_count)
            # Update preparation_printed_qty on the DB lines if we can find them
            if self.order_id:
                for item in lines_to_print:
                    luuid = item.get('line_uuid', '')
                    if luuid:
                        ol = self.order_id.lines.filtered(lambda l: l.uuid == luuid)
                        if ol:
                            ol[0].preparation_printed_qty += item.get('qty', 0)
                            ol[0].preparation_printed = True
        else:
            self.error_message = error
            _logger.warning("[PrepDirect retry] Job #%s — retry #%s failed: %s",
                            self.id, self.retry_count, error)
            self._send_failure_alert(error)

        return True

    @api.model
    def action_retry_failed_jobs(self):
        """
        Cron entry point — automatically retry failed jobs.

        Only retries jobs whose printer has retry_count > 0 and whose
        last attempt was at least retry_interval minutes ago.
        Each failed job is retried at most retry_count times total.
        """
        from datetime import timedelta
        now = fields.Datetime.now()

        failed_jobs = self.search([('state', '=', 'failed')])
        for job in failed_jobs:
            printer = job.printer_id
            if not printer or printer.retry_count <= 0:
                continue
            if job.retry_count >= printer.retry_count:
                _logger.info(
                    "[PrepDirect cron] Job #%s exceeded max retries (%s/%s) — giving up",
                    job.id, job.retry_count, printer.retry_count)
                continue
            # Check interval — use printed_date or create_date as last-attempt time
            last_attempt = job.printed_date or job.create_date
            minutes_since = (now - last_attempt).total_seconds() / 60
            if minutes_since < printer.retry_interval:
                continue
            _logger.info(
                "[PrepDirect cron] Retrying job #%s for printer %s (attempt %s/%s)",
                job.id, printer.name, job.retry_count + 1, printer.retry_count)
            job.action_retry()

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
        _logger.info("Cleaned up %d old preparation jobs", len(old_jobs))
