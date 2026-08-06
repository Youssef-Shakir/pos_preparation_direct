import logging
from collections import defaultdict
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


class PosPreparationDirectController(http.Controller):

    @http.route('/pos_preparation_direct/print', type='json', auth='user')
    def print_preparation(self, config_id, lines, table_name='', floor_name='',
                          waiter_name='', order_name='', customer_note='', order_id=False):
        """
        Direct print preparation tickets from POS frontend.

        Args:
            config_id: POS configuration ID
            lines: List of dicts with product_id, product_name, qty, note
            table_name: Table number/name
            floor_name: Floor name
            waiter_name: Waiter/Employee name
            order_name: Order reference
            customer_note: Special instructions
            order_id: Optional order ID to link jobs

        Returns:
            dict with success status and job counts
        """
        config = request.env['pos.config'].browse(config_id)

        if not config.exists():
            return {'success': False, 'error': 'Invalid POS configuration'}

        if not config.use_preparation_printing:
            return {'success': True, 'job_count': 0, 'message': 'Preparation printing disabled'}

        if not config.preparation_printer_ids:
            return {'success': True, 'job_count': 0, 'message': 'No printers configured'}

        # Group lines by printer based on product category
        printer_lines = defaultdict(list)
        Product = request.env['product.product']

        for line in lines:
            product_id = line.get('product_id')
            if not product_id:
                continue

            product = Product.browse(product_id)
            if not product.exists():
                continue

            pos_categ = product.pos_categ_ids[:1] if product.pos_categ_ids else None
            if not pos_categ:
                continue

            # Get printers for this category that are in the POS config
            printers = config.preparation_printer_ids & pos_categ.preparation_printer_ids
            for printer in printers:
                printer_lines[printer].append({
                    'product_name': line.get('product_name', product.name),
                    'qty': line.get('qty', 1),
                    'note': line.get('note', ''),
                })

        if not printer_lines:
            return {'success': True, 'job_count': 0, 'message': 'No items to print'}

        # Create jobs and print
        Job = request.env['pos.preparation.job']
        order = request.env['pos.order'].browse(order_id) if order_id else None

        success_count = 0
        failed_count = 0
        errors = []

        for printer, plines in printer_lines.items():
            if not plines:
                continue

            ticket_data = {
                'table_name': table_name,
                'floor_name': floor_name,
                'waiter_name': waiter_name,
                'order_name': order_name,
                'customer_note': customer_note,
                'lines': plines,
            }

            # Create job record
            job_vals = {
                'printer_id': printer.id,
                'order_name': order_name,
                'state': 'pending',
                'line_data': plines,
                'table_name': table_name,
                'floor_name': floor_name,
                'waiter_name': waiter_name,
                'customer_note': customer_note,
            }
            if order and order.exists():
                job_vals['order_id'] = order.id

            job = Job.create(job_vals)

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
                errors.append(f"{printer.name}: {error}")

        result = {
            'success': failed_count == 0,
            'job_count': success_count + failed_count,
            'success_count': success_count,
            'failed_count': failed_count,
        }

        if errors:
            result['errors'] = errors

        return result
