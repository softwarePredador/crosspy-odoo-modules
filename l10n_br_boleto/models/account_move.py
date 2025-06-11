import base64
import datetime
import json
import logging
import urllib

import requests
from odoo import _, fields, models, api
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)

now = datetime.date.today()

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    has_bank_slip = fields.Boolean(string='Has Bank Slip', default=False)
    bank_slip_id = fields.One2many(
        string='Boleto',
        comodel_name="bank.slip",
        inverse_name="invoice_line_id",
        readonly=True,
        index=True,
    )
    id_integracao = fields.Char(string='Id Integração', readonly=True)

    @api.ondelete(at_uninstall=False)
    def _prevent_delete_issued_slip(self):
        if any(child.status == 'EMITIDO' for child in self.mapped('bank_slip_id')):
            raise UserError("You can't delete this record: it has issued slip records.")

class AccountMove(models.Model):
    _inherit = "account.move"

    has_bank_slip = fields.Boolean(copy=False, default=False)
    bank_slip_ids = fields.One2many("bank.slip", "invoice")

    shipping = fields.Binary(string="Arquivo Remessa",
                             readonly=True, copy=False)
    shipping_name = fields.Char(string="Nome do Arquivo", readonly=True)

    def action_post(self):
        res = super().action_post()
        if self.env.user.has_group('l10n_br_boleto.group_auto_slip_create') and self.has_bank_slip:
            self.bank_slip_step()      
        return res
    
    def cancel_bank_slip(self, id_integracao):
        bank_slip_id = self.env["bank.slip"].search([("id_integracao", "=", id_integracao)])
        bank_slip_id.write({"active": False})
        bank_slip_id.update_status()
        self.env["account.move.line"].search(
            [("id_integracao", "=", id_integracao)]).write(
            {"has_bank_slip": False, "bank_slip_id": False, "id_integracao": False}
        )
        return

    def bank_slip_step(self):
        """Gera boletos desde a Fatura Odoo"""
        if not self.has_bank_slip:
            return False
        if not self.line_ids.filtered(lambda x:x.account_type in ('asset_receivable','liability_payable')):
            self.env.user.notify_warning(_("Não há linhas de faturamento para gerar o boleto."))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'danger',
                    'message': _("Não há linhas de faturamento para gerar o boleto."),
                }
            }

        for line in self.line_ids.filtered(lambda x: x.account_type in ('asset_receivable','liability_payable') and (x.date_maturity or fields.Date.today()) < (fields.Date.today() + datetime.timedelta(days=0))):
            # self.env.user.notify_warning(_("A data de vencimento: %s não pode ser utilizada, pois a data atual é menor que 2 dias (Tempo necessário para registro e emissão do boleto: %s - %s).") % (self.format_date_str(line.date_maturity), line.move_id.name, line.name))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'danger',
                    'message': _("A data de vencimento: %s não pode ser utilizada, pois a data atual é menor que 2 dias (Tempo necessário para registro e emissão do boleto: %s - %s).") % (self.format_date_str(line.date_maturity), line.move_id.name, line.name),
                }
            }

        slip_msg = []
        for line in self.line_ids.filtered(lambda x: x.account_type in ('asset_receivable','liability_payable') and (x.date_maturity or fields.Date.today()) >= (fields.Date.today() + datetime.timedelta(days=0))):
            if not line.has_bank_slip or not line.bank_slip_id:
                self.prepare_bank_slip(line)
            else:
                slip_msg.append(line.name)
            
        if len(slip_msg):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'danger',
                    'message': _("Boleto já gerado para a linha %s." % ',\n'.join(slip_msg)),
                }
            }

    def prepare_bank_slip(self, line, force=False):
        # if the line has a bank slip, return
        if line.has_bank_slip and line.bank_slip_id:
            return
        else:
            if not line.date_maturity:
                line.date_maturity = fields.Date.today()
            # if date_maturity is saturdays or sundays, add next working day
            if line.date_maturity.weekday() == 5:
                date_maturity = line.date_maturity + datetime.timedelta(days=2)
            elif line.date_maturity.weekday() == 6:
                date_maturity = line.date_maturity + datetime.timedelta(days=1)
            else:
                date_maturity = line.date_maturity

            # date_juros_multa is a 1 day after the date_maturity, but not friday, saturdays or sundays
            if date_maturity.weekday() == 4:
                date_juros_multa = date_maturity + datetime.timedelta(days=3)
            elif date_maturity.weekday() == 5:
                date_juros_multa = date_maturity + datetime.timedelta(days=2)
            else:
                date_juros_multa = date_maturity + datetime.timedelta(days=1)

            if not force and date_maturity < (now + datetime.timedelta(days=0)):
                raise ValidationError(
                    _("A data de vencimento: %s não pode ser utilizada, pois a data atual é menor (Tempo necessário para registro e emissão do boleto).") % self.format_date_str(date_maturity))
            else:
                vals = {}
                ids = []
                vals["TituloDataEmissao"] = self.format_date_str(
                    self.invoice_date)
                nameRot = line.name.replace("/", "-")
                if len(nameRot) > 10:
                    nameRot = line.name.replace("INV", "FAT")
                vals["Rotulo"] = nameRot
                vals["TituloDataVencimento"] = self.format_date_str(
                    date_maturity)
                vals["TituloDataMulta"] = self.format_date_str(
                    date_juros_multa)
                vals["TituloDataJuros"] = self.format_date_str(
                    date_juros_multa)
                vals["TituloValor"] = round(line.debit, 2)
                vals["InvoiceLineID"] = line
                bank_slip_id = self.create_bank_slip(vals)
                line.has_bank_slip = True
                line.id_integracao = bank_slip_id.id

                return bank_slip_id

    def create_bank_slip(self, vals):
        # data = self.consult_bank_slip_ids(id_integracao, self.company_id)
        # if data["_dados"][0]["situacao"] == 'FALHA':
        #     raise ValidationError(data["_dados"][0]["motivo"])
        pdf_name = vals["Rotulo"].replace("/", "-") + ".pdf"
        vals = {
            "name": self.name + ' (' + self.partner_id.name + ') - ' + vals["Rotulo"],
            "company_id": self.company_id.id,
            # "id_integracao": data["_dados"][0]["IdIntegracao"],
            "invoice": self.id,
            "invoice_line_id": vals["InvoiceLineID"].id,
            "invoice_name": vals["Rotulo"],
            "customer": self.partner_id.id,
            "status": 'REGISTRADO',
            # "pdf": self.convertToBase64(data["_dados"][0]["UrlBoleto"]),
            "pdf_name": pdf_name,
            "value": vals["TituloValor"],
            "due_date": datetime.datetime.strptime(
                str(vals["TituloDataVencimento"]
                    ).replace(" 00:00:00", ""),
                "%d/%m/%Y",
            ),
        }
        bank_slip_id = self.env["bank.slip"].create(vals)
        # if bank_slip_id:
        #     bank_slip_id.make_pdf()
        return bank_slip_id


    def valida_sacado(self):
        sacado = self.partner_id
        msgSacado = 'Os dados cadastrais do destinário estão incompletos. Favor preencher os seguintes campos:\n'
        msgCount = 0

        if not sacado:
            raise ValidationError("Nenhum cliente / sacado encontrado.")
        else:
            if not sacado.country_id.code == 'BR':
                raise ValidationError(
                    "Somente é emitido boleto para clientes do Brasil.")
            else:
                if not sacado.cnpj_cpf:
                    msgSacado += '\n - CNPJ / CPF;'
                    msgCount += 1
                if not sacado.legal_name:
                    msgSacado += '\n - Razão Social / Nome Completo;'
                    msgCount += 1
                if not sacado.zip:
                    msgSacado += '\n - CEP;'
                    msgCount += 1
                if not sacado.street_name:
                    msgSacado += '\n - Logradouro (Nome da Rua);'
                    msgCount += 1
                if not sacado.street_number:
                    msgSacado += '\n - Número da Rua;'
                    msgCount += 1
                if not sacado.district:
                    msgSacado += '\n - Bairro;'
                    msgCount += 1
                if not sacado.state_id:
                    msgSacado += '\n - Estado;'
                    msgCount += 1
                if not sacado.city_id:
                    msgSacado += '\n - Cidade;'
                    msgCount += 1
                if not sacado.email:
                    msgSacado += '\n - E-mail;'
                    msgCount += 1
        if msgCount > 0:
            raise ValidationError(msgSacado)

    def consult_bank_slip_ids(self, id_integracao, company_id):
        if not company_id.plugboleto_cnpj_cedente:
            raise ValidationError(
                _("CNPJ Cedente não informado na empresa %s.") % company_id.name)
        tecnospeed = self.env["payment.acquirer"].search(
            [("provider", "=", "tecnospeed_boletos")]
        )
        api_url = tecnospeed.tecnospeed_get_form_action_url(self.company_id)
        data = requests.request(
            "GET",
            api_url + "/api/v1/boletos?IdIntegracao=" + id_integracao,
            headers=self.headers(company_id),
        )
        return json.loads(data.text)

    def create_bank_slip2(self, id_integracao, value, rotulo, invoiceline):
        data = self.consult_bank_slip_ids(id_integracao, self.company_id)
        if data["_dados"][0]["situacao"] == 'FALHA':
            raise ValidationError(data["_dados"][0]["motivo"])
        pdf_name = rotulo.replace("/", "-") + ".pdf"
        vals = {
            "name": self.name + ' (' + self.partner_id.name + ') - ' + rotulo,
            "company_id": self.company_id.id,
            "id_integracao": data["_dados"][0]["IdIntegracao"],
            "invoice": self.id,
            "invoice_line_id": invoiceline.id,
            "invoice_name": rotulo,
            "customer": self.partner_id.id,
            "status": data["_dados"][0]["situacao"],
            "pdf": self.convertToBase64(data["_dados"][0]["UrlBoleto"]),
            "pdf_name": pdf_name,
            "value": value,
            "due_date": datetime.datetime.strptime(
                str(data["_dados"][0]["TituloDataVencimento"]
                    ).replace(" 00:00:00", ""),
                "%d/%m/%Y",
            ),
        }
        self.env["bank.slip"].create(vals)
        return

    def generate_shipping_all(self):
        selected_ids = self.env.context.get('active_ids', [])
        selected_ids.clear()
        company_id = self.company_id
        for bank_slip in self.bank_slip_ids:
            selected_ids.append(bank_slip.id)
            company_id = bank_slip.company_id
        _logger.warning("selected_ids - generate_shipping_all: %s", selected_ids)
        self.env["bank.slip"].download_bank_slip_shipping(selected_ids, company_id)
        return
        
    def format_date_str(self, date):
        return datetime.date.strftime(date, "%d/%m/%Y")

    def format_date(self, date):
        return datetime.datetime.strptime(date, "%d/%m/%Y")

    def format_value(self, value):
        return str(value).replace(".", ",")

    def convertToBase64(self, url):
        page = urllib.request.Request(url)
        encoded_string = base64.b64encode(urllib.request.urlopen(page).read()).decode(
            "utf-8"
        )
        return encoded_string

    def headers(self, company_id):
        if not company_id.plugboleto_cnpj_cedente:
            raise ValidationError(
                _("CNPJ Cedente não informado na empresa %s.") % company_id.name)

        tecnospeed = self.env["payment.acquirer"].search(
            [("provider", "=", "tecnospeed_boletos")]
        )
        if not tecnospeed:
            raise ValidationError(_("Não há método de pagamento cadastrado"))

        headers = {
            "Content-Type": "application/json",
            "cnpj-sh": tecnospeed.tecnospeed_cnpj_sh,
            "token-sh": tecnospeed.tecnospeed_token_sh,
            "cnpj-cedente": company_id.plugboleto_cnpj_cedente
        }
        return headers
