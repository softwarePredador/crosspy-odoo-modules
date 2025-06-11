#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 Tiny SPRL (<http://tiny.be>).
#    Thinkopen - Brasil
#    Copyright (C) Thinkopen Solutions (<http://www.thinkopensolutions.com.br>)
#    Akretion
#    Copyright (C) Akretion (<http://www.akretion.com>)
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

import re
import logging
import base64
from datetime import datetime

from odoo import models, fields, api, _
from odoo.tools import config

_logger = logging.getLogger(__name__)

try:
    from OpenSSL import crypto
except ImportError:
    _logger.error('Cannot import OpenSSL.crypto', exc_info=True)


class ResCompany(models.Model):
    _inherit = 'res.company'



class Company(models.Model):
    _name = "res.company"
    _inherit = [_name, "format.address.mixin", "l10n_br_base.party.mixin"]

    def _get_company_address_field_names(self):
        partner_fields = super()._get_company_address_field_names() 
        return partner_fields + [
                                        "legal_name",
                                        "inscr_est",
                                        "inscr_mun",
                                        "district",
                                        "city_id",
                                        "suframa",
                                        "state_tax_number_ids",
                                    ]

    def _inverse_legal_name(self):
        """Write the l10n_br specific functional fields."""
        for company in self:
            company.partner_id.legal_name = company.legal_name

    def _inverse_district(self):
        """Write the l10n_br specific functional fields."""
        for company in self:
            company.partner_id.district = company.district

    def _inverse_state(self):
        """Write the l10n_br specific functional fields."""
        for company in self:
            company.partner_id.state_id = company.state_id.id

    def _inverse_state_tax_number_ids(self):
        """Write the l10n_br specific functional fields."""
        for company in self:
            state_tax_number_ids = self.env["state.tax.numbers"]
            for ies in company.state_tax_number_ids:
                state_tax_number_ids |= ies
            company.partner_id.state_tax_number_ids = state_tax_number_ids

    def _inverse_inscr_mun(self):
        """Write the l10n_br specific functional fields."""
        for company in self:
            company.partner_id.inscr_mun = company.inscr_mun

    def _inverse_inscr_est(self):
        """Write the l10n_br specific functional fields."""
        for company in self:
            company.partner_id.inscr_est = company.inscr_est

    def _inverse_city_id(self):
        """Write the l10n_br specific functional fields."""
        for company in self:
            company.partner_id.city_id = company.city_id

    def _inverse_country_enforce_cities(self):
        """Write the l10n_br specific functional fields."""
        for company in self:
            company.partner_id.country_id.country_enforce_cities = company.country_enforce_cities

    def _inverse_suframa(self):
        """Write the l10n_br specific functional fields."""
        for company in self:
            company.partner_id.suframa = company.suframa


    legal_name = fields.Char(
        compute="_compute_address",
        inverse="_inverse_legal_name",
    )

    district = fields.Char(
        compute="_compute_address",
        inverse="_inverse_district",
    )

    city_id = fields.Many2one(
        domain="[('state_id', '=', state_id)]",
        compute="_compute_address",
        inverse="_inverse_city_id",
    )

    # country_id = fields.Many2one(default=lambda self: self.env.ref("base.br"))
    country_enforce_cities = fields.Boolean(
        compute="_compute_address",
        inverse="_inverse_country_enforce_cities",
    )

    inscr_est = fields.Char(
        compute="_compute_address",
        inverse="_inverse_inscr_est",
    )

    state_tax_number_ids = fields.One2many(
        string="State Tax Numbers",
        comodel_name="state.tax.numbers",
        inverse_name="partner_id",  # FIXME
        compute="_compute_address",
        inverse="_inverse_state_tax_number_ids",
    )

    inscr_mun = fields.Char(
        compute="_compute_address",
        inverse="_inverse_inscr_mun",
    )

    suframa = fields.Char(
        compute="_compute_address",
        inverse="_inverse_suframa",
    )

    @api.model
    def _fields_view_get(
        self, view_id=None, view_type="form", toolbar=False, submenu=False
    ):
        res = super()._fields_view_get(view_id, view_type, toolbar, submenu)
        if view_type == "form":
            res["arch"] = self._fields_view_get_address(res["arch"])
        return res

    def write(self, values):
        try:
            result = super().write(values)
        except Exception as e:
            if not config["without_demo"] and values.get("currency_id"):
                # required for demo installation
                result = models.Model.write(self, values)
            else:
                raise e

        return result

    l10n_br_certificate = fields.Binary('Certificado A1')
    l10n_br_cert_password = fields.Char('Senha certificado', size=64)

    l10n_br_cert_state = fields.Selection(
        [('not_loaded', 'Not loaded'),
         ('expired', 'Expired'),
         ('invalid_password', 'Invalid Password'),
         ('unknown', 'Unknown'),
         ('valid', 'Valid')],
        string="Cert. State", compute='_compute_expiry_date',
        default='not_loaded')
    l10n_br_cert_information = fields.Text(
        string="Cert. Info", compute='_compute_expiry_date')
    l10n_br_cert_expire_date = fields.Date(
        string="Cert. Expiration Date", compute='_compute_expiry_date')

    @api.onchange('zip')
    def onchange_mask_zip(self):
        if self.zip:
            val = re.sub('[^0-9]', '', self.zip)
            if len(val) == 8:
                zip = "%s-%s" % (val[0:5], val[5:8])
                self.zip = zip

    def _compute_expiry_date(self):
        for company in self:
            company.l10n_br_cert_state = 'unknown'
            company.l10n_br_cert_information = ''
            company.l10n_br_cert_expire_date = None
            try:
                pfx = base64.b64decode(
                    company.with_context(bin_size=False).l10n_br_certificate)
                pfx = crypto.load_pkcs12(pfx, company.l10n_br_cert_password)
                cert = pfx.get_certificate()
                end = datetime.strptime(
                    cert.get_notAfter().decode(), '%Y%m%d%H%M%SZ')
                subj = cert.get_subject()
                company.l10n_br_cert_expire_date = end.date()
                if datetime.now() < end:
                    company.l10n_br_cert_state = 'valid'
                else:
                    company.l10n_br_cert_state = 'expired'
                company.l10n_br_cert_information = "%s\n%s\n%s\n%s" % (
                    subj.CN, subj.L, subj.O, subj.OU)
            except crypto.Error:
                company.l10n_br_cert_state = 'invalid_password'
            except:
                _logger.warning(
                    _(u'Unknown error when validating certificate'),
                    exc_info=True)

