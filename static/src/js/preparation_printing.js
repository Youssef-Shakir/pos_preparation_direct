/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { rpc } from "@web/core/network/rpc";

console.log("[PrepDirectPrint] Module loading...");

patch(PosStore.prototype, {

    async sendOrderInPreparation(order, cancelled = false) {
        console.log("[PrepDirectPrint] sendOrderInPreparation called", {
            orderUuid: order?.uuid,
            orderId: order?.id,
            cancelled,
        });

        // Call original first so the order gets a server ID if it doesn't have one
        const result = await super.sendOrderInPreparation(order, cancelled);

        if (cancelled) {
            console.log("[PrepDirectPrint] Skipping — order cancellation flow");
            return result;
        }

        await this._sendToDirectPreparationPrinters(order);
        return result;
    },

    async _sendToDirectPreparationPrinters(order) {
        if (!order) {
            console.log("[PrepDirectPrint] No order — aborting");
            return;
        }
        if (!this.config?.use_preparation_printing) {
            console.log("[PrepDirectPrint] Preparation printing not enabled");
            return;
        }

        const lines = order.get_orderlines?.() || [];
        if (!lines.length) {
            console.log("[PrepDirectPrint] No lines on order");
            return;
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

            if (result.errors?.length > 0) {
                console.warn("[PrepDirectPrint] Some prints failed:", result.errors);
            }
        } catch (error) {
            console.error("[PrepDirectPrint] Error sending to preparation:", error);
        }
    },
});

console.log("[PrepDirectPrint] Module loaded successfully");
