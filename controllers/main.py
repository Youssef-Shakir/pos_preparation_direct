import logging
from collections import defaultdict
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


def _safe_int(value):
    """Return int(value) if value is a real integer, else None."""
    try:
        v = int(value)
        # Reject Odoo local IDs like 'pos.order.line_3' — int() silently fails
        # those, but they arrive as strings so int() would already have raised.
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


class PosPreparationDirectController(http.Controller):

    @http.route('/pos_preparation_direct/print', type='json', auth='user')
    def print_preparation(self, config_id, lines, table_name='', floor_name='',
                          waiter_name='', order_name='', customer_note='',
                          order_id=False, order_uuid=''):

        _logger.info("[PrepDirect] ── print request ── order_id=%s  order_uuid=%s  lines=%d",
                     order_id, order_uuid, len(lines))

        config = request.env['pos.config'].browse(config_id)
        if not config.exists():
            return {'success': False, 'error': 'Invalid POS configuration'}
        if not config.use_preparation_printing:
            return {'success': True, 'job_count': 0, 'message': 'Preparation printing disabled'}
        if not config.preparation_printer_ids:
            return {'success': True, 'job_count': 0, 'message': 'No printers configured'}

        # ── Resolve server-side order (optional — for linking jobs only) ──────
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
            _logger.info("[PrepDirect] DB order found id=%s, lines: %s",
                         pos_order.id,
                         [(ol.id, ol.uuid, ol.product_id.name,
                           ol.qty, ol.preparation_printed_qty)
                          for ol in pos_order.lines])

        # ── Job-based dedup: sum qty already printed for each line_uuid ───────
        # This works even when the order/lines weren't in the DB at print time.
        # Jobs are created before printing, so they're always the earliest record.
        printed_by_line_uuid = {}
        if order_uuid:
            past_jobs = request.env['pos.preparation.job'].search([
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
        printer_lines = defaultdict(list)   # printer → [{product_name, qty, note, line_uuid}]
        line_updates  = defaultdict(list)   # printer → [(pos.order.line, qty_printed)]
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

            # Best-effort: find server-side line record for additional dedup
            lid = _safe_int(line.get('line_id', 0))
            ol  = line_by_id.get(lid) if lid else None
            if ol is None and line_uuid:
                ol = line_by_uuid.get(line_uuid)

            # Dedup: how much was already sent to kitchen for this line?
            already_printed = printed_by_line_uuid.get(line_uuid, 0)
            if ol and ol.preparation_printed_qty > already_printed:
                # DB field is more up-to-date (e.g. after a retry or manual resend)
                already_printed = ol.preparation_printed_qty

            qty = js_qty - already_printed

            _logger.info(
                "[PrepDirect] %s  line_uuid=%s  js_qty=%.1f  already_printed=%.1f  → qty=%.1f  "
                "(ol=%s  ol.prep_qty=%.1f)",
                product.name, line_uuid, js_qty, already_printed, qty,
                ol.id if ol else None,
                ol.preparation_printed_qty if ol else 0)

            if qty == 0:
                _logger.info("[PrepDirect] %s — skipping (already printed)", product.name)
                continue

            printers = config.preparation_printer_ids & pos_categ.preparation_printer_ids
            for printer in printers:
                printer_lines[printer].append({
                    'product_name': line.get('product_name', product.name),
                    'qty':          qty,
                    'note':         line.get('note', ''),
                    'line_uuid':    line_uuid,   # stored in job for future dedup
                })
                if ol:
                    line_updates[printer].append((ol, qty))

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
                'order_uuid':    order_uuid,      # ← key for cross-tab dedup
                'state':         'pending',
                'line_data':     plines,          # includes line_uuid per item
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
                # Also update preparation_printed_qty on the DB line (secondary mechanism)
                for ol, qty_printed in line_updates.get(printer, []):
                    _logger.info("[PrepDirect] Updating preparation_printed_qty on line %s: "
                                 "%.1f + %.1f", ol.id, ol.preparation_printed_qty, qty_printed)
                    ol.preparation_printed_qty += qty_printed
                    ol.preparation_printed = True
            else:
                job.state = 'failed'
                job.error_message = error
                failed_count += 1
                errors.append(f"{printer.name}: {error}")
                _logger.warning("[PrepDirect] Print failed on %s: %s", printer.name, error)

        _logger.info("[PrepDirect] Done — success=%d  failed=%d", success_count, failed_count)

        result = {
            'success':       failed_count == 0,
            'job_count':     success_count + failed_count,
            'success_count': success_count,
            'failed_count':  failed_count,
        }
        if errors:
            result['errors'] = errors
        return result
