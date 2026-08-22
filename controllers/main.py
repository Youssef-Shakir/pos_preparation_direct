import logging
from collections import defaultdict
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


class PosPreparationDirectController(http.Controller):

    @http.route('/pos_preparation_direct/print', type='json', auth='user')
    def print_preparation(self, config_id, lines, table_name='', floor_name='',
                          waiter_name='', order_name='', customer_note='',
                          order_id=False, order_uuid=''):

        _logger.info("[PrepDirect] ── print_preparation called ──────────────────")
        _logger.info("[PrepDirect] order_id=%s  order_uuid=%s", order_id, order_uuid)
        _logger.info("[PrepDirect] lines received (%d): %s", len(lines), lines)

        config = request.env['pos.config'].browse(config_id)
        if not config.exists():
            return {'success': False, 'error': 'Invalid POS configuration'}
        if not config.use_preparation_printing:
            return {'success': True, 'job_count': 0, 'message': 'Preparation printing disabled'}
        if not config.preparation_printer_ids:
            return {'success': True, 'job_count': 0, 'message': 'No printers configured'}

        # ── Resolve order on server (try id first, then uuid) ────────────────
        pos_order = None
        if order_id:
            pos_order = request.env['pos.order'].browse(int(order_id))
            if not pos_order.exists():
                pos_order = None
                _logger.info("[PrepDirect] order_id %s not found in DB", order_id)
            else:
                _logger.info("[PrepDirect] Order found by id: %s (uuid=%s)",
                             pos_order.id, pos_order.uuid)

        if not pos_order and order_uuid:
            pos_order = request.env['pos.order'].search(
                [('uuid', '=', order_uuid)], limit=1)
            if pos_order:
                _logger.info("[PrepDirect] Order found by uuid: %s", pos_order.id)
            else:
                _logger.info("[PrepDirect] Order NOT found by uuid=%s — will trust JS qty", order_uuid)

        # ── Build lookup map: DB id → order line ─────────────────────────────
        # Keyed by DB id (int) — most reliable after order is synced.
        # Also build uuid map as fallback.
        line_by_id   = {}
        line_by_uuid = {}
        if pos_order:
            for ol in pos_order.lines:
                line_by_id[ol.id] = ol
                if hasattr(ol, 'uuid') and ol.uuid:
                    line_by_uuid[ol.uuid] = ol
            _logger.info("[PrepDirect] Order lines in DB: %s",
                         [(ol.id, getattr(ol, 'uuid', '?'),
                           ol.product_id.name, ol.qty, ol.preparation_printed_qty)
                          for ol in pos_order.lines])

        # ── Group lines by printer ────────────────────────────────────────────
        printer_lines = defaultdict(list)
        line_updates  = defaultdict(list)   # printer → [pos.order.line] to mark after print
        Product = request.env['product.product']

        for line in lines:
            product_id = line.get('product_id')
            if not product_id:
                _logger.info("[PrepDirect] Skipping line — no product_id")
                continue

            product = Product.browse(product_id)
            if not product.exists():
                _logger.info("[PrepDirect] Skipping line — product %s not found", product_id)
                continue

            pos_categ = product.pos_categ_ids[:1] if product.pos_categ_ids else None
            if not pos_categ:
                _logger.info("[PrepDirect] Skipping %s — no POS category", product.name)
                continue

            # Resolve the server-side order line record
            line_id   = line.get('line_id', 0)
            line_uuid = line.get('line_uuid', '')
            ol = line_by_id.get(int(line_id)) if line_id else None
            if ol is None and line_uuid:
                ol = line_by_uuid.get(line_uuid)

            if ol:
                # Use server-side delta — prevents double-print from other tablets
                server_qty = ol.qty - ol.preparation_printed_qty
                _logger.info(
                    "[PrepDirect] %s: line_id=%s line_uuid=%s "
                    "ol.qty=%.1f ol.preparation_printed_qty=%.1f → server_qty=%.1f  (JS sent qty=%.1f)",
                    product.name, line_id, line_uuid,
                    ol.qty, ol.preparation_printed_qty, server_qty, line.get('qty', 1))
                qty = server_qty
            else:
                qty = line.get('qty', 1)
                _logger.info(
                    "[PrepDirect] %s: no server line found (line_id=%s line_uuid=%s) "
                    "— trusting JS qty=%.1f",
                    product.name, line_id, line_uuid, qty)

            if qty == 0:
                _logger.info("[PrepDirect] %s: qty=0 after dedup — skipping", product.name)
                continue

            printers = config.preparation_printer_ids & pos_categ.preparation_printer_ids
            for printer in printers:
                printer_lines[printer].append({
                    'product_name': line.get('product_name', product.name),
                    'qty': qty,
                    'note': line.get('note', ''),
                })
                if ol:
                    line_updates[printer].append(ol)

        if not printer_lines:
            _logger.info("[PrepDirect] Nothing to print after dedup")
            return {'success': True, 'job_count': 0, 'message': 'No items to print'}

        # ── Print ─────────────────────────────────────────────────────────────
        Job = request.env['pos.preparation.job']
        success_count = 0
        failed_count  = 0
        errors        = []

        for printer, plines in printer_lines.items():
            if not plines:
                continue

            ticket_data = {
                'table_name':    table_name,
                'floor_name':    floor_name,
                'waiter_name':   waiter_name,
                'order_name':    order_name,
                'customer_note': customer_note,
                'lines':         plines,
            }

            job_vals = {
                'printer_id':    printer.id,
                'order_name':    order_name,
                'state':         'pending',
                'line_data':     plines,
                'table_name':    table_name,
                'floor_name':    floor_name,
                'waiter_name':   waiter_name,
                'customer_note': customer_note,
            }
            if pos_order and pos_order.exists():
                job_vals['order_id'] = pos_order.id

            job = Job.create(job_vals)
            success, error = printer.print_ticket(ticket_data)

            if success:
                job.state = 'printed'
                job.printed_date = fields.Datetime.now()
                success_count += 1
                # Mark lines as printed on the server so other tablets see the correct delta
                for ol in line_updates.get(printer, []):
                    _logger.info("[PrepDirect] Marking printed: line %s qty=%.1f", ol.id, ol.qty)
                    ol.preparation_printed_qty = ol.qty
                    ol.preparation_printed = True
            else:
                job.state = 'failed'
                job.error_message = error
                failed_count += 1
                errors.append(f"{printer.name}: {error}")
                _logger.warning("[PrepDirect] Print failed on %s: %s", printer.name, error)

        _logger.info("[PrepDirect] Done — success=%d failed=%d", success_count, failed_count)

        result = {
            'success':       failed_count == 0,
            'job_count':     success_count + failed_count,
            'success_count': success_count,
            'failed_count':  failed_count,
        }
        if errors:
            result['errors'] = errors
        return result
