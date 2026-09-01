import logging
from collections import defaultdict
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    preparation_printed = fields.Boolean(string='Sent to Preparation', default=False)
    preparation_printed_qty = fields.Float(string='Printed Quantity', default=0.0)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    preparation_job_ids = fields.One2many(
        'pos.preparation.job',
        'order_id',
        string='Preparation Jobs'
    )
    preparation_job_count = fields.Integer(
        string='Job Count',
        compute='_compute_preparation_job_count'
    )

    @api.depends('preparation_job_ids')
    def _compute_preparation_job_count(self):
        for order in self:
            order.preparation_job_count = len(order.preparation_job_ids)

    def _get_preparation_printers_for_line(self, line):
        """
        Get the preparation printers for a given order line.
        Returns printers based on product category assignment.
        """
        printers = self.env['pos.preparation.printer']

        # Get the product's POS category
        product = line.product_id
        if not product:
            return printers

        pos_categ = product.pos_categ_ids[:1] if product.pos_categ_ids else None
        if not pos_categ:
            return printers

        # Get printers assigned to this category that are also in the POS config
        config_printers = self.config_id.preparation_printer_ids
        category_printers = pos_categ.preparation_printer_ids

        return config_printers & category_printers

    def create_preparation_jobs(self, lines_data=None):
        """
        Create and send preparation jobs for order lines.

        Args:
            lines_data: Optional list of dicts with line data from frontend.
                       If None, uses order lines from database.

        Returns:
            dict with job_count, success_count, failed_count
        """
        self.ensure_one()

        config = self.config_id
        if not config.use_preparation_printing:
            return {'job_count': 0, 'success_count': 0, 'failed_count': 0}

        if not config.preparation_printer_ids:
            return {'job_count': 0, 'success_count': 0, 'failed_count': 0}

        # Collect metadata
        table_name = ''
        floor_name = ''
        if hasattr(self, 'table_id') and self.table_id:
            table_name = self.table_id.table_number or self.table_id.name or ''
            if self.table_id.floor_id:
                floor_name = self.table_id.floor_id.name or ''

        waiter_name = ''
        if self.user_id:
            waiter_name = self.user_id.name
        elif hasattr(self, 'employee_id') and self.employee_id:
            waiter_name = self.employee_id.name

        customer_note = ''
        if hasattr(self, 'note'):
            customer_note = self.note or ''

        # Group lines by printer
        printer_lines = defaultdict(list)

        if lines_data:
            # Use data from frontend (direct print before order sync)
            for line_data in lines_data:
                product_id = line_data.get('product_id')
                if not product_id:
                    continue

                product = self.env['product.product'].browse(product_id)
                pos_categ = product.pos_categ_ids[:1] if product.pos_categ_ids else None

                if pos_categ:
                    printers = config.preparation_printer_ids & pos_categ.preparation_printer_ids
                    for printer in printers:
                        printer_lines[printer].append({
                            'product_name': line_data.get('product_name', product.name),
                            'qty': line_data.get('qty', 1),
                            'note': line_data.get('note', ''),
                        })
        else:
            # Use order lines from database
            for line in self.lines:
                # Calculate new quantity to print
                new_qty = line.qty - line.preparation_printed_qty
                if new_qty == 0:
                    continue

                printers = self._get_preparation_printers_for_line(line)
                for printer in printers:
                    printer_lines[printer].append({
                        'product_name': line.full_product_name or line.product_id.name,
                        'qty': new_qty,
                        'note': line.note or '',
                    })

                # Mark as printed
                line.preparation_printed = True
                line.preparation_printed_qty = line.qty

        # Create jobs and print
        Job = self.env['pos.preparation.job']
        success_count = 0
        failed_count = 0

        for printer, lines in printer_lines.items():
            if not lines:
                continue

            # Prepare ticket data
            # Convert current time to user's timezone for correct printing
            now_utc = fields.Datetime.now()
            now_local = fields.Datetime.context_timestamp(self, now_utc)
            print_time = now_local.strftime('%Y-%m-%d %H:%M')

            ticket_data = {
                'table_name': table_name,
                'floor_name': floor_name,
                'waiter_name': waiter_name,
                'order_name': self.pos_reference or self.name,
                'customer_note': customer_note,
                'lines': lines,
                'print_time': print_time,
            }

            # Create job record
            job = Job.create({
                'printer_id': printer.id,
                'order_id': self.id,
                'order_name': self.pos_reference or self.name,
                'state': 'pending',
                'line_data': lines,
                'table_name': table_name,
                'floor_name': floor_name,
                'waiter_name': waiter_name,
                'customer_note': customer_note,
            })

            # Print directly
            success, error = printer.print_ticket(ticket_data)

            if success:
                job.state = 'printed'
                job.printed_date = fields.Datetime.now()
                success_count += 1
            else:
                job.state = 'failed'
                job.error_message = error
                failed_count += 1
                _logger.warning(f"Failed to print preparation ticket: {error}")

        return {
            'job_count': success_count + failed_count,
            'success_count': success_count,
            'failed_count': failed_count,
        }

    def action_view_preparation_jobs(self):
        """View preparation jobs for this order."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Preparation Jobs',
            'res_model': 'pos.preparation.job',
            'view_mode': 'list,form',
            'domain': [('order_id', '=', self.id)],
        }

    def action_resend_to_preparation(self):
        """Manually resend all lines to preparation printers."""
        self.ensure_one()
        # Reset printed status
        for line in self.lines:
            line.preparation_printed = False
            line.preparation_printed_qty = 0

        return self.create_preparation_jobs()
