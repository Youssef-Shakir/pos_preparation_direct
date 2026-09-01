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

        # ── Build lookup map: DB-line id / uuid → pos.order.line ─────────────
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

        # ── Job-based dedup: sum qty already printed per (printer, line_uuid) ──
        # Tracked PER PRINTER so a product sent to both Kitchen and Bar doesn't
        # double-count — Kitchen's jobs don't inflate Bar's already_printed and
        # vice versa, which would produce spurious negative (cancellation) lines.
        printed_by_printer_uuid = {}  # {printer_id: {line_uuid: qty}}
        if order_uuid:
            past_jobs = Job.search([
                ('order_uuid', '=', order_uuid),
                ('state', '=', 'printed'),
            ])
            for job in past_jobs:
                pid = job.printer_id.id
                if pid not in printed_by_printer_uuid:
                    printed_by_printer_uuid[pid] = {}
                for item in (job.line_data or []):
                    luuid = item.get('line_uuid', '')
                    if luuid:
                        d = printed_by_printer_uuid[pid]
                        d[luuid] = d.get(luuid, 0) + item.get('qty', 0)
            _logger.info("[PrepDirect] Already printed by (printer_id, line_uuid): %s",
                         printed_by_printer_uuid)

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

            # Try to find the DB line record
            lid = _safe_int(line.get('line_id', 0))
            ol  = line_by_id.get(lid) if lid else None
            if ol is None and line_uuid:
                ol = line_by_uuid.get(line_uuid)

            printers = config.preparation_printer_ids & pos_categ.preparation_printer_ids
            if not printers:
                _logger.info("[PrepDirect] %s — no matching printer for category %s",
                             product.name, pos_categ.name)
                continue

            for printer in printers:
                # Per-printer dedup: each printer tracks its own history independently.
                # This prevents a product assigned to two printers from double-counting
                # and producing a spurious negative (cancellation) delta.
                already_printed = printed_by_printer_uuid.get(printer.id, {}).get(line_uuid, 0)

                qty = js_qty - already_printed

                _logger.info(
                    "[PrepDirect] %s → %s  uuid=%s  js_qty=%.1f  already=%.1f  → send=%.1f",
                    product.name, printer.name, line_uuid, js_qty, already_printed, qty)

                if qty == 0:
                    _logger.info("[PrepDirect] %s → %s — skip (fully printed)",
                                 product.name, printer.name)
                    continue

                # Safety: never generate a spurious cancellation.
                # A negative delta when js_qty >= 0 means already_printed is wrong
                # (e.g. stale DB data). Only print negatives when the POS itself
                # sent a negative qty (explicit deletion/cancellation line).
                if qty < 0 and js_qty >= 0:
                    _logger.warning(
                        "[PrepDirect] %s → %s: delta %.1f is negative but js_qty=%.1f "
                        "— skipping spurious cancellation",
                        product.name, printer.name, qty, js_qty)
                    continue

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

        # ── Print — one job per printer ───────────────────────────────────────
        success_count = 0
        failed_count  = 0
        errors        = []

        for printer, plines in printer_lines.items():
            if not plines:
                continue

            # Ticket number per printer job
            try:
                ticket_number = int(
                    request.env['ir.sequence'].next_by_code(
                        'pos.preparation.job.ticket') or 0)
            except Exception:
                ticket_number = 0

            # Convert current time to user's timezone for correct printing
            now_utc = fields.Datetime.now()
            now_local = fields.Datetime.context_timestamp(request.env.user, now_utc)
            print_time = now_local.strftime('%Y-%m-%d %H:%M')

            ticket_data = {
                'table_name':    table_name,
                'floor_name':    floor_name,
                'waiter_name':   waiter_name,
                'order_name':    order_name,
                'customer_note': customer_note,
                'lines':         plines,
                'ticket_number': ticket_number,
                'print_time':    print_time,
            }

            job_vals = {
                'printer_id':       printer.id,
                'order_name':       order_name,
                'order_uuid':       order_uuid,
                # print_request_id is per (request, printer) — unique constraint
                # is UNIQUE(print_request_id, printer_id) so multiple printers
                # in one request each get their own row safely.
                'print_request_id': print_request_id or False,
                'ticket_number':    ticket_number,
                'state':            'pending',
                'line_data':        plines,
                'table_name':       table_name,
                'floor_name':       floor_name,
                'waiter_name':      waiter_name,
                'customer_note':    customer_note,
            }
            if pos_order and pos_order.exists():
                job_vals['order_id'] = pos_order.id

            # Use a savepoint so an IntegrityError on this printer's job
            # does NOT roll back jobs already created for other printers.
            sp = f"job_create_{printer.id}"
            try:
                request.env.cr.execute(f"SAVEPOINT {sp}")
                job = Job.create(job_vals)
                request.env.cr.execute(f"RELEASE SAVEPOINT {sp}")
            except IntegrityError:
                request.env.cr.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                _logger.warning(
                    "[PrepDirect] Duplicate request_id=%s for printer %s — skipping",
                    print_request_id, printer.name)
                # Count as success (already printed this request for this printer)
                success_count += 1
                continue

            # Send to printer; catch exceptions so job record is never lost
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
                _logger.info("[PrepDirect] Printed ticket #%s on %s",
                             ticket_number, printer.name)
                for ol, qty_printed in line_updates.get(printer, []):
                    ol.preparation_printed_qty += qty_printed
                    ol.preparation_printed = True
            else:
                job.state = 'failed'
                job.error_message = error
                failed_count += 1
                errors.append(f"{printer.name}: {error}")
                _logger.warning(
                    "[PrepDirect] Print failed on %s: %s", printer.name, error)
                job._send_failure_alert(error)

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
