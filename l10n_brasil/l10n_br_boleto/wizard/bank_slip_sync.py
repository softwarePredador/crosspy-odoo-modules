# -*- coding: utf-8 -*-
from odoo import models, fields

class BankSlipSync(models.TransientModel):
    _name = 'bank.slip.sync.wizard'
    _description = 'Bank Slip Date Range Wizard'

    start_date = fields.Date(string="Start Date", required=True)
    end_date = fields.Date(string="End Date", required=True)

    def action_sync_normal(self):
        self.env['bank.slip'].bbrasil_sync_slips(slip_type='A', start_date=self.start_date , end_date=self.end_date)

    def action_sync_processed(self):
        self.env['bank.slip'].bbrasil_sync_slips(slip_type='B', start_date=self.start_date , end_date=self.end_date)
