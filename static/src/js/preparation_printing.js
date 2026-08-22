/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { rpc } from "@web/core/network/rpc";

console.log("[PrepDirectPrint] Module loading...");

// Track sent quantities per order to avoid duplicate prints
const preparationSentQty = {};

/**
 * Patch PosStore to send preparation tickets to ESC/POS printers
 */
patch(PosStore.prototype, {

    /**
     * Override sendOrderInPreparation to send to direct ESC/POS printers
     */
    async sendOrderInPreparation(order, cancelled = false) {
        console.log("[PrepDirectPrint] sendOrderInPreparation called", { order: order?.uuid, cancelled });

        // Call original method first
        const result = await super.sendOrderInPreparation(order, cancelled);

        // Don't print if this is a cancellation
        if (cancelled) {
            console.log("[PrepDirectPrint] Skipping - cancellation");
            return result;
        }

        // Send to preparation printers
        await this._sendToDirectPreparationPrinters(order);

        return result;
    },

    /**
     * Get lines that have new quantity to send to preparation
     */
    _getUnsentDirectPreparationLines(order) {
        const orderUuid = order.uuid;
        if (!preparationSentQty[orderUuid]) {
            preparationSentQty[orderUuid] = {};
        }
        const sentQty = preparationSentQty[orderUuid];

        const unsent = [];
        for (const line of order.get_orderlines()) {
            const lineSentQty = sentQty[line.uuid] || 0;
            const currentQty = line.get_quantity();
            const newQty = currentQty - lineSentQty;

            if (newQty !== 0) {
                unsent.push({
                    line: line,
                    newQty: newQty,
                    totalQty: currentQty,
                });
            }
        }
        return unsent;
    },

    /**
     * Mark lines as sent to preparation
     */
    _markDirectLinesSentToPreparation(order) {
        const orderUuid = order.uuid;
        if (!preparationSentQty[orderUuid]) {
            preparationSentQty[orderUuid] = {};
        }

        for (const line of order.get_orderlines()) {
            preparationSentQty[orderUuid][line.uuid] = line.get_quantity();
        }
    },

    /**
     * Send the current order to ESC/POS preparation printers (direct print)
     */
    async _sendToDirectPreparationPrinters(order) {
        console.log("[PrepDirectPrint] _sendToDirectPreparationPrinters called");

        if (!order) {
            console.log("[PrepDirectPrint] No order provided");
            return;
        }

        // Check if preparation printing is enabled
        console.log("[PrepDirectPrint] Config:", this.config);
        console.log("[PrepDirectPrint] use_preparation_printing:", this.config?.use_preparation_printing);

        if (!this.config || !this.config.use_preparation_printing) {
            console.log("[PrepDirectPrint] Preparation printing not enabled");
            return;
        }

        // Get lines that haven't been sent yet
        const unsentLines = this._getUnsentDirectPreparationLines(order);
        console.log("[PrepDirectPrint] Unsent lines:", unsentLines.length);

        if (unsentLines.length === 0) {
            console.log("[PrepDirectPrint] No unsent lines");
            return;
        }

        try {
            // Build line data
            const linesData = unsentLines.map(({ line, newQty }) => ({
                product_id: line.product_id?.id || line.product_id,
                product_name: line.get_full_product_name(),
                qty: newQty,
                note: line.note || "",
                line_uuid: line.uuid || "",
            }));

            // Get table info if available
            let tableName = "";
            let floorName = "";
            if (order.table_id) {
                tableName = order.table_id.table_number?.toString() || order.table_id.name || "";
                if (order.table_id.floor_id) {
                    floorName = order.table_id.floor_id.name || "";
                }
            }

            // Get waiter name
            let waiterName = "";
            if (this.cashier) {
                waiterName = this.cashier.name || "";
            }

            // Get order name/reference
            const orderName = order.name || order.pos_reference || `Order ${order.uuid?.substring(0, 8) || ""}`;

            // Get customer note
            const customerNote = order.note || order.customer_note || "";

            console.log("[PrepDirectPrint] Sending RPC with lines:", linesData);

            const result = await rpc("/pos_preparation_direct/print", {
                config_id: this.config.id,
                lines: linesData,
                table_name: tableName,
                floor_name: floorName,
                waiter_name: waiterName,
                order_name: orderName,
                customer_note: customerNote,
                order_uuid: order.uuid || "",
            });

            console.log("[PrepDirectPrint] RPC result:", result);

            if (result.success || result.job_count > 0) {
                // Mark lines as sent
                this._markDirectLinesSentToPreparation(order);
            }

            if (result.errors && result.errors.length > 0) {
                console.warn("[PrepDirectPrint] Some prints failed:", result.errors);
            }

        } catch (error) {
            console.error("[PrepDirectPrint] Error sending to preparation:", error);
        }
    },
});

console.log("[PrepDirectPrint] Module loaded successfully");
