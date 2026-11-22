/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { Navbar } from "@point_of_sale/app/navbar/navbar";
import { rpc } from "@web/core/network/rpc";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { ConnectionLostError } from "@web/core/network/rpc";

console.log("[PrepDirectPrint] Module loading...");

/**
 * Quantity of items added to the order but not yet sent to the kitchen.
 * (Odoo's own `count` nets additions against removals, so it can be 0 while
 * new items are still waiting; removals never print a ticket here anyway.)
 */
function unsentQuantity(pos, order) {
    const changes = pos.getOrderChanges(false, order);
    return Object.values(changes?.orderlines || {}).reduce(
        (sum, line) => sum + (line.quantity > 0 ? line.quantity : 0),
        0
    );
}

patch(PosStore.prototype, {

    async sendOrderInPreparation(order, cancelled = false) {
        console.log("[PrepDirectPrint] sendOrderInPreparation called", {
            orderUuid: order?.uuid,
            orderId: order?.id,
            cancelled,
        });

        // Core marks every line as "sent" (updateLastOrderChange) BEFORE our
        // print request goes out, and edits that record in place. Keep a copy
        // so the lines can be marked unsent again if the kitchen never got them.
        const sentBefore = order?.last_order_preparation_change
            ? JSON.parse(JSON.stringify(order.last_order_preparation_change))
            : null;

        // Call original first so the order gets a server ID if it doesn't have one
        const result = await super.sendOrderInPreparation(order, cancelled);

        if (cancelled) {
            console.log("[PrepDirectPrint] Skipping — order cancellation flow");
            return result;
        }

        const outcome = await this._sendToDirectPreparationPrinters(order);
        if (!outcome.ok) {
            this._markOrderUnsent(order, sentBefore);
            this._warnKitchenNotReceived(outcome);
        }
        return result;
    },

    /**
     * Put the order back to its state before "Order" was pressed, so the
     * Order button stays active and the waiter can send it again. Sending
     * again is safe: the server only prints what it has not printed yet.
     */
    _markOrderUnsent(order, sentBefore) {
        if (!order || !sentBefore) {
            return;
        }
        order.last_order_preparation_change = sentBefore;
        order.setDirty?.();
        if (order.id) {
            this.addPendingOrder([order.id]);
        }
        console.warn("[PrepDirectPrint] Kitchen did not confirm — order marked as not sent", order.uuid);
    },

    _warnKitchenNotReceived(outcome) {
        const body = outcome.network
            ? "لم يصل الطلب إلى المطبخ بسبب انقطاع الاتصال.\n" +
              "تأكد من الواي فاي ثم اضغط زر الطلب (Order) مرة أخرى.\n\n" +
              "The kitchen did not receive the order (connection problem). " +
              "Check the Wi-Fi and press Order again."
            : "لم تتم طباعة الطلب على: " + (outcome.printers || "طابعة المطبخ") + "\n" +
              "اضغط زر الطلب (Order) مرة أخرى، وإذا تكرر الخطأ تأكد من الطابعة.\n\n" +
              "The order was not printed on: " + (outcome.printers || "kitchen printer") +
              ". Press Order again; if it fails again, check the printer.";
        this.dialog.add(AlertDialog, {
            title: "⚠️ الطلب لم يُرسل / Order not sent",
            body,
        });
    },

    /**
     * Returns {ok: true} when the kitchen got everything (or there was
     * nothing to print), otherwise {ok: false, network, printers}.
     */
    async _sendToDirectPreparationPrinters(order) {
        if (!order) {
            console.log("[PrepDirectPrint] No order — aborting");
            return { ok: true };
        }
        if (!this.config?.use_preparation_printing) {
            console.log("[PrepDirectPrint] Preparation printing not enabled");
            return { ok: true };
        }

        const lines = order.get_orderlines?.() || [];
        if (!lines.length) {
            console.log("[PrepDirectPrint] No lines on order");
            return { ok: true };
        }

        // Each print attempt gets a unique ID.
        // The server's UNIQUE(print_request_id) constraint makes this the
        // primary guard against double-prints from network retries or double-clicks.
        // Cross-tab dedup is handled server-side via job records keyed by order_uuid.
        const printRequestId = (typeof crypto !== "undefined" && crypto.randomUUID)
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(36).substring(2, 10)}`;

        // Send TOTAL quantities for every line — no client-side delta tracking.
        // The server subtracts already-printed qty from past job records and
        // decides what (if anything) needs printing.
        const linesData = lines.map(line => {
            const lineId = typeof line.id === "number" ? line.id : 0;
            const entry = {
                product_id:   line.product_id?.id || line.product_id,
                product_name: line.get_full_product_name?.() || "",
                qty:          line.get_quantity(),   // TOTAL, not delta
                note:         line.note || "",
                line_id:      lineId,
                line_uuid:    line.uuid || "",
            };
            return entry;
        });

        let tableName = "";
        let floorName = "";
        if (order.table_id) {
            tableName = order.table_id.table_number?.toString() || order.table_id.name || "";
            floorName = order.table_id.floor_id?.name || "";
        }

        const waiterName   = this.cashier?.name || "";
        const orderName    = order.name
            || order.pos_reference
            || (order.uuid ? `#${order.uuid.substring(0, 8)}` : "New Order");
        const customerNote = order.note || order.customer_note || "";

        console.log("[PrepDirectPrint] Sending RPC:", {
            orderId: order.id,
            orderUuid: order.uuid,
            orderName,
            printRequestId,
            linesCount: linesData.length,
            lines: linesData,
        });

        try {
            const result = await rpc("/pos_preparation_direct/print", {
                config_id:        this.config.id,
                lines:            linesData,
                table_name:       tableName,
                floor_name:       floorName,
                waiter_name:      waiterName,
                order_name:       orderName,
                customer_note:    customerNote,
                order_uuid:       order.uuid || "",
                order_id:         typeof order.id === "number" ? order.id : 0,
                print_request_id: printRequestId,
            });

            console.log("[PrepDirectPrint] RPC result:", result);

            if (result?.errors?.length > 0) {
                console.warn("[PrepDirectPrint] Some prints failed:", result.errors);
                const printers = result.errors.map((e) => String(e).split(":")[0]).join("، ");
                return { ok: false, network: false, printers };
            }
            if (result && result.success === false) {
                return { ok: false, network: false, printers: "" };
            }
            return { ok: true };
        } catch (error) {
            console.error("[PrepDirectPrint] Error sending to preparation:", error);
            return { ok: false, network: true };
        }
    },
});

/**
 * Warn when the waiter leaves a table (Plan button) with items that were
 * never sent to the kitchen. Leaving a table saves the items on the server
 * but does not print them, so they used to print hours later.
 */
patch(Navbar.prototype, {
    onClickPlanButton() {
        const pos = this.pos;
        const order = pos.get_order();
        let unsent = 0;
        try {
            if (pos.config.module_pos_restaurant && order && !order.finalized) {
                unsent = unsentQuantity(pos, order);
            }
        } catch (e) {
            console.warn("[PrepDirectPrint] Could not check unsent items:", e);
        }
        if (!unsent) {
            return super.onClickPlanButton(...arguments);
        }

        const leave = () => super.onClickPlanButton(...arguments);
        pos.dialog.add(ConfirmationDialog, {
            title: "⚠️ أصناف لم تُرسل للمطبخ / Items not sent",
            body:
                `يوجد ${unsent} صنف لم يتم إرساله إلى المطبخ.\n` +
                "هل تريد إرسالها الآن؟\n\n" +
                `${unsent} item(s) were not sent to the kitchen. Send them now?`,
            confirmLabel: "إرسال الآن / Send now",
            cancelLabel: "خروج بدون إرسال / Leave",
            confirm: async () => {
                try {
                    await pos.sendOrderInPreparationUpdateLastChange(order);
                } catch (error) {
                    if (!(error instanceof ConnectionLostError)) {
                        throw error;
                    }
                    // Odoo shows its own offline popup; stay on the table.
                    return;
                }
                // Leave only when everything reached the kitchen. If it did
                // not, the "Order not sent" warning is shown and the waiter
                // stays on the table with the Order button still active.
                if (!(unsentQuantity(pos, order) > 0)) {
                    leave();
                }
            },
            cancel: () => leave(),
            // Closing the popup (X / outside) keeps the waiter on the table.
            dismiss: () => {},
        });
    },
});

console.log("[PrepDirectPrint] Module loaded successfully");
