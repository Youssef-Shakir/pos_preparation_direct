import logging
from collections import defaultdict
from psycopg2 import IntegrityError
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


def _safe_int(value):
    """Return int(value) if value is a real integer, else None."""
    try:
        v = int(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


class PosPreparationDirectController(http.Controller):

    @http.route('/pos_preparation_direct/print', type='json', auth='user')
    def print_preparation(self, config_id, lines, table_name='', floor_name='',
                          waiter_name='', order_name='', customer_note='',
                          order_id=False, order_uuid='', print_request_id=''):

        _logger.info(
            "[PrepDirect] ── print request ── order_id=%s  order_uuid=%s  "
            "lines=%d  request_id=%s",
            order_id, order_uuid, len(lines), print_request_id)

        Job = request.env['pos.preparation.job']

        # ── Idempotency: reject duplicate requests ────────────────────────────
        # Two tabs firing at the same millisecond each generate a unique
        # print_request_id. The UNIQUE constraint on that column means only
        # one INSERT wins; the other gets IntegrityError → return early.
        if print_request_id:
            existing = Job.sudo().search(
                [('print_request_id', '=', print_request_id)], limit=1)
            if existing:
                _logger.info(
                    "[PrepDirect] Duplicate request_id=%s — already job #%s state=%s",
                    print_request_id, existing.ticket_number, existing.state)
                return {
                    'success': existing.state == 'printed',
                    'job_count': 1,
                    'duplicate': True,
                    'ticket_number': existing.ticket_number,
                }

        config = request.env['pos.config'].browse(config_id)
        if not config.exists():
            return {'success': False, 'error': 'Invalid POS configuration'}
        if not config.use_preparation_printing:
            return {'success': True, 'job_count': 0, 'message': 'Preparation printing disabled'}
        if not config.preparation_printer_ids:
            return {'success': True, 'job_count': 0, 'message': 'No printers configured'}

        # ── Normalise order name — never blank ───────────────────────────────
        if not order_name and order_uuid:
            order_name = f"#{order_uuid[:8]}"

        # ── Resolve server-side order (optional — for linking jobs) ──────────
        pos_order = None
        oid = _safe_int(order_id)
        if oid:
            pos_order = request.env['pos.order'].browse(oid)
            if not pos_order.exists():
                pos_order = None
        if not pos_order and order_uuid:
            pos_order = request.env['pos.order'].search(
                [('uuid', '=', order_uuid)], limit=1) or None

        # ── Build lookup map: DB-line-id / uuid → pos.order.line ─────────────
        line_by_id   = {}
        line_by_uuid = {}
        if pos_order:
            for ol in pos_order.lines:
                line_by_id[ol.id] = ol
                if ol.uuid:
                    line_by_uuid[ol.uuid] = ol
            _logger.info(
                "[PrepDirect] DB order id=%s, lines: %s",
                pos_order.id,
                [(ol.id, ol.uuid, ol.product_id.name,
                  ol.qty, ol.preparation_printed_qty)
                 for ol in pos_order.lines])

        # ── Job-based dedup: sum qty already printed for each line_uuid ───────
        printed_by_line_uuid = {}
        if order_uuid:
            past_jobs = Job.search([
                ('order_uuid', '=', order_uuid),
                ('state', '=', 'printed'),
            ])
            for job in past_jobs:
                for item in (job.line_data or []):
                    luuid = item.get('line_uuid', '')
                    if luuid:
                        printed_by_line_uuid[luuid] = (
                            printed_by_line_uuid.get(luuid, 0) + item.get('qty', 0)
                        )
            _logger.info("[PrepDirect] Printed by line_uuid from past jobs: %s",
                         printed_by_line_uuid)

        # ── Group lines by printer ────────────────────────────────────────────
        printer_lines = defaultdict(list)
        line_updates  = defaultdict(list)
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
                _logger.info("[PrepDirect] Skipping %s — no POS category", product.name)
                continue

            line_uuid = line.get('line_uuid', '')
            js_qty    = line.get('qty', 1)

            lid = _safe_int(line.get('line_id', 0))
            ol  = line_by_id.get(lid) if lid else None
            if ol is None and line_uuid:
                ol = line_by_uuid.get(line_uuid)

            already_printed = printed_by_line_uuid.get(line_uuid, 0)
            if ol and ol.preparation_printed_qty > already_printed:
                already_printed = ol.preparation_printed_qty

            qty = js_qty - already_printed

            _logger.info(
                "[PrepDirect] %s  line_uuid=%s  js_qty=%.1f  already_printed=%.1f  → qty=%.1f",
                product.name, line_uuid, js_qty, already_printed, qty)

            if qty == 0:
                _logger.info("[PrepDirect] %s — skipping (already printed)", product.name)
                continue

            printers = config.preparation_printer_ids & pos_categ.preparation_printer_ids
            for printer in printers:
                printer_lines[printer].append({
                    'product_name': line.get('product_name', product.name),
                    'qty':          qty,
                    'note':         line.get('note', ''),
                    'line_uuid':    line_uuid,
                })
                if ol:
                    line_updates[printer].append((ol, qty))

        if not printer_lines:
            _logger.info("[PrepDirect] Nothing to print after dedup")
            return {'success': True, 'job_count': 0, 'message': 'No items to print'}

        # ── Print ─────────────────────────────────────────────────────────────
        success_count = 0
        failed_count  = 0
        errors        = []

        for printer, plines in printer_lines.items():
            if not plines:
                continue

            # Allocate ticket number BEFORE creating job so it's on the receipt
            try:
                ticket_number = int(
                    request.env['ir.sequence'].next_by_code(
                        'pos.preparation.job.ticket') or 0)
            except Exception:
                ticket_number = 0

            ticket_data = {
                'table_name':    table_name,
                'floor_name':    floor_name,
                'waiter_name':   waiter_name,
                'order_name':    order_name,
                'customer_note': customer_note,
                'lines':         plines,
                'ticket_number': ticket_number,
            }

            job_vals = {
                'printer_id':      printer.id,
                'order_name':      order_name,
                'order_uuid':      order_uuid,
                'print_request_id': print_request_id or False,
                'ticket_number':   ticket_number,
                'state':           'pending',
                'line_data':       plines,
                'table_name':      table_name,
                'floor_name':      floor_name,
                'waiter_name':     waiter_name,
                'customer_note':   customer_note,
            }
            if pos_order and pos_order.exists():
                job_vals['order_id'] = pos_order.id

            # Create job record first — ensures it's always visible even if
            # print fails or an exception is raised during socket send.
            try:
                job = Job.create(job_vals)
            except IntegrityError:
                # Race: another concurrent request already created a job with
                # this print_request_id. Rollback savepoint and skip.
                request.env.cr.rollback()
                _logger.warning(
                    "[PrepDirect] Concurrent duplicate request_id=%s — skipping",
                    print_request_id)
                return {'success': True, 'job_count': 0, 'duplicate': True}

            # Send to printer; catch all exceptions so job record is never lost
            try:
                success, error = printer.print_ticket(ticket_data)
            except Exception as exc:
                success = False
                error = str(exc)
                _logger.exception(
                    "[PrepDirect] Unexpected error printing to %s", printer.name)

            if success:
                job.state = 'printed'
                job.printed_date = fields.Datetime.now()
                success_count += 1
                for ol, qty_printed in line_updates.get(printer, []):
                    _logger.info(
                        "[PrepDirect] Updating preparation_printed_qty on line %s: "
                        "%.1f + %.1f", ol.id, ol.preparation_printed_qty, qty_printed)
                    ol.preparation_printed_qty += qty_printed
                    ol.preparation_printed = True
            else:
                job.state = 'failed'
                job.error_message = error
                failed_count += 1
                errors.append(f"{printer.name}: {error}")
                _logger.warning(
                    "[PrepDirect] Print failed on %s: %s", printer.name, error)

        _logger.info(
            "[PrepDirect] Done — success=%d  failed=%d", success_count, failed_count)

        result = {
            'success':       failed_count == 0,
            'job_count':     success_count + failed_count,
            'success_count': success_count,
            'failed_count':  failed_count,
        }
        if errors:
            result['errors'] = errors
        return result
