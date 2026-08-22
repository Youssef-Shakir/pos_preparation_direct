/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { rpc } from "@web/core/network/rpc";

console.log("[PrepDirectPrint] Module loading...");

// Track sent quantities per order to avoid duplicate prints (within same tab session)
const preparationSentQty = {};

patch(PosStore.prototype, {

    async sendOrderInPreparation(order, cancelled = false) {
        console.log("[PrepDirectPrint] sendOrderInPreparation called", {
            orderUuid: order?.uuid,
            orderId: order?.id,
            cancelled,
        });

        // Call original first — this syncs the order to the server and assigns server IDs
        const result = await super.sendOrderInPreparation(order, cancelled);

        if (cancelled) {
            console.log("[PrepDirectPrint] Skipping — cancellation");
            return result;
        }

        await this._sendToDirectPreparationPrinters(order);
        return result;
    },

    _getUnsentDirectPreparationLines(order) {
        const orderUuid = order.uuid;
        if (!preparationSentQty[orderUuid]) {
            preparationSentQty[orderUuid] = {};
        }
        const sentQty = preparationSentQty[orderUuid];

        const unsent = [];
        for (const line of order.get_orderlines()) {
            // Use line.id (server DB id) as key — more stable than uuid across tabs
            const lineKey = line.id || line.uuid;
            const lineSentQty = sentQty[lineKey] || 0;
            const currentQty = line.get_quantity();
            const newQty = currentQty - lineSentQty;

            console.log("[PrepDirectPrint] Line check:", {
                lineKey,
                lineId: line.id,
                lineUuid: line.uuid,
                product: line.get_full_product_name?.() || line.product_id?.display_name,
                currentQty,
                lineSentQty,
                newQty,
            });

            if (newQty !== 0) {
                unsent.push({ line, newQty, totalQty: currentQty });
            }
        }

        console.log("[PrepDirectPrint] Unsent lines count:", unsent.length);
        return unsent;
    },

    _markDirectLinesSentToPreparation(order) {
        const orderUuid = order.uuid;
        if (!preparationSentQty[orderUuid]) {
            preparationSentQty[orderUuid] = {};
        }
        for (const line of order.get_orderlines()) {
            const lineKey = line.id || line.uuid;
            preparationSentQty[orderUuid][lineKey] = line.get_quantity();
        }
        console.log("[PrepDirectPrint] Marked sent:", preparationSentQty[orderUuid]);
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

        const unsentLines = this._getUnsentDirectPreparationLines(order);
        if (unsentLines.length === 0) {
            console.log("[PrepDirectPrint] Nothing to print");
            return;
        }

        try {
            const linesData = unsentLines.map(({ line, newQty }) => {
                // line.id in Odoo 18 is a real integer when synced from server,
                // or a string like 'pos.order.line_3' for locally-created lines.
                // Only send line_id when it's a proper integer.
                const lineId = typeof line.id === 'number' ? line.id : 0;
                const entry = {
                    product_id: line.product_id?.id || line.product_id,
                    product_name: line.get_full_product_name?.() || "",
                    qty: newQty,
                    note: line.note || "",
                    line_id: lineId,
                    line_uuid: line.uuid || "",
                };
                console.log("[PrepDirectPrint] Line to send:", entry);
                return entry;
            });

            let tableName = "";
            let floorName = "";
            if (order.table_id) {
                tableName = order.table_id.table_number?.toString() || order.table_id.name || "";
                floorName = order.table_id.floor_id?.name || "";
            }

            const waiterName = this.cashier?.name || "";
            const orderName = order.name || order.pos_reference || `Order ${order.uuid?.substring(0, 8) || ""}`;
            const customerNote = order.note || order.customer_note || "";

            // After super.sendOrderInPreparation(), the order should have a server ID
            console.log("[PrepDirectPrint] Sending RPC — order details:", {
                orderId: order.id,
                orderUuid: order.uuid,
                linesCount: linesData.length,
                lines: linesData,
            });

            const result = await rpc("/pos_preparation_direct/print", {
                config_id: this.config.id,
                lines: linesData,
                table_name: tableName,
                floor_name: floorName,
                waiter_name: waiterName,
                order_name: orderName,
                customer_note: customerNote,
                order_uuid: order.uuid || "",
                order_id: typeof order.id === 'number' ? order.id : 0,
            });

            console.log("[PrepDirectPrint] RPC result:", result);

            if (result.success || result.job_count > 0) {
                this._markDirectLinesSentToPreparation(order);
            }

            if (result.errors?.length > 0) {
                console.warn("[PrepDirectPrint] Some prints failed:", result.errors);
            }

        } catch (error) {
            console.error("[PrepDirectPrint] Error sending to preparation:", error);
        }
    },
});

console.log("[PrepDirectPrint] Module loaded successfully");
