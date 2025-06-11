# -*- coding: utf-8 -*-
from odoo import models, fields

from odoo import  _, api, fields, models
from odoo.exceptions import ValidationError

class Picking(models.Model):
    _inherit = "stock.picking"

    is_invoice_pending = fields.Boolean("Is Invoice pending", compute="_check_is_invoice_pending")
    invoice_not_approved_ids = fields.One2many("account.move", string="Invoices with error", compute='_compute_pending_process')
    edoc_not_authorized_ids = fields.One2many("eletronic.document", string="Edi with error", compute='_compute_pending_process')
    order_with_pending_invoice_ids = fields.One2many("sale.order", string="Sale with error", compute='_compute_pending_process')

    @api.depends('state')
    def _check_is_invoice_pending(self):
        for pick in self:
            pick.is_invoice_pending = all([move.sale_line_id.qty_to_invoice >= move.product_qty for move in pick.move_ids_without_package])

    def action_invoice_picking(self):
        res = False
        if self.is_invoice_pending:
            sales = self.mapped('move_ids_without_package').mapped('sale_line_id.order_id')
            res = sales.create_invoice_inmediate()
        return res

    @api.depends('move_ids_without_package.sale_line_id.invoice_lines.move_id.state',
                 'move_ids_without_package.sale_line_id')
    def _compute_pending_process(self):
        for pick in self:
            pick.invoice_not_approved_ids = pick.move_ids_without_package.sale_line_id.invoice_lines.move_id.filtered(lambda x: x.state not in ('posted', 'cancel'))
            pick.edoc_not_authorized_ids = pick.move_ids_without_package.sale_line_id.invoice_lines.move_id.edoc_ids.filtered(lambda x: x.state not in ('done', 'cancel'))
            pick.order_with_pending_invoice_ids = pick.move_ids_without_package.sale_line_id.filtered(lambda x: x.invoice_status == 'to invoice').mapped('order_id')

    def open_invoice_not_approved_ids(self):
        moves = self.invoice_not_approved_ids
        action = self.env["ir.actions.actions"]._for_xml_id("account.action_move_line_form")
        action['domain'] = [('id', 'in', moves.ids)]
        return action
    
    def open_edoc_not_autorized_ids(self):
        edocs = self.edoc_not_authorized_ids
        action = self.env["ir.actions.actions"]._for_xml_id("l10n_br_eletronic_document.action_view_eletronic_document")
        action['domain'] = [('id', 'in', edocs.ids)]
        return action
    
    def open_sale_order_invoice_pending(self):
        orders = self.order_with_pending_invoice_ids
        action = self.env["ir.actions.actions"]._for_xml_id("sale.action_orders")
        action['domain'] = [('id', 'in', orders.ids)]
        return action
    
    

class StockPackageType(models.Model):
    _inherit = "stock.package.type"

    package_weight = fields.Float(string="Peso da Embalagem")
