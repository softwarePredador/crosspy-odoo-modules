# -*- coding: utf-8 -*-

import base64
import datetime
import json
import logging
import re

import requests
from odoo import fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

class BankSlipMove(models.Model):
    _name = 'bank.slip.move'
    _description = "Bank Slip Moves"
    _check_company_auto = True

    name = fields.Char(string="Name")
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        related='slip_id.company_id'
    )

    process = fields.Selection(string='Process', selection=[('auto', 'Automatic'),
                                                            ('manual', 'Manual'), 
                                                            ('processed', 'Processed'), 
                                                            ('error', 'Error')], default='auto')
    slip_id = fields.Many2one("bank.slip", string="Bank Slip")
    invoice_line_id = fields.Many2one(
        string="Linha da Fatura",
        comodel_name="account.move.line",
        related="slip_id.invoice_line_id",
        inverse='_inverse_invoice_line_id',
        domain="[('account_internal_type', '=', 'receivable'), ('debit', '>', 0), ('company_id', '=', current_company_id), ('partner_id', '=', cobro_partner_id)]",
        index=True,
    )
    cobro_partner_id = fields.Many2one("res.partner", string="Client", readonly=True)
    cobro_partner_name = fields.Char(string='Nome no Retorno', readonly=True,)
    cobro_cnpj_cpf = fields.Char(string='CNPJ/CPF no Retorno', readonly=True,)
    cobro_invoice_number = fields.Char(string='No. Fatura no Retorno', readonly=True,)

    value = fields.Float(string="Valor", readonly=True)
    shipping_id = fields.Many2one('bank.slip.shipping', string='Bank Slip Shipping')
    move_code_id = fields.Many2one('bank.slip.return.code', string="Move Code")
    message = fields.Char(string="Message")
    date = fields.Date(string="Move Date")
    date_maturity = fields.Date(string='Due Date', index=True,
        help="This field is used for payable and receivable journal entries. You can put the limit date for the payment of this line.")

    def _inverse_invoice_line_id(self):
        for slip_move in self:
            if not slip_move.invoice_line_id.bank_slip_id and slip_move.invoice_line_id:
                slip_move.invoice_line_id.move_id.prepare_bank_slip(slip_move.invoice_line_id, force=True)
            slip_move.slip_id = slip_move.invoice_line_id.bank_slip_id

    def slip_move_process(self):
        if self.move_code_id.name == '02':
            self.slip_id.state = 'CONFIRMED'
            self.process = 'processed'
        elif self.move_code_id.name == '06':
            self.slip_id.state = 'LIQUIDADO'
            #Create & Reconcile invoice payment
            if self.invoice_line_id.reconciled:
                return
            difference = - (self.value - self.invoice_line_id.amount_residual)
            payment_vals = {
                            'journal_id': self.shipping_id.bank_account_id.journal_id.id,
                            'payment_date': self.date,
                            'communication': '{} Retorno: {}'.format(self.slip_id.invoice_line_id.name, self.shipping_id.name),
                            'amount': self.value,
                            }

            if not self.invoice_line_id.currency_id.is_zero(difference):
                payment_vals.update({
                                    'payment_difference': difference,
                                    'payment_difference_handling': 'reconcile',
                                    'writeoff_account_id': 337,
                                    'writeoff_label': 'Juros de boleto {}'.format(self.slip_id.display_name)
                })
            payment = self.env['account.payment.register'].with_context(active_model='account.move.line', active_ids=self.invoice_line_id.ids).new(payment_vals)
            payment._create_payments()

            self.process = 'processed'
        