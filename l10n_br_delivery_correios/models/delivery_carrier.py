# -*- coding: utf-8 -*-
import base64
import io
import time
import logging
import phonenumbers

from odoo.http import request, content_disposition

from .correios_request import CorreiosProvider
from markupsafe import Markup
from odoo.tools.zeep.helpers import serialize_object

from odoo import api, models, fields, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_repr
from odoo.tools.safe_eval import const_eval

_logger = logging.getLogger(__name__)

try:
    from erpbrasil.base.fiscal import cnpj_cpf, ie
except ImportError:
    _logger.error("Biblioteca erpbrasil.base não instalada")



class Providercorreios(models.Model):
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(selection_add=[
        ('correios', "Correios")
    ], ondelete={'correios': lambda recs: recs.write({'delivery_type': 'fixed', 'fixed_price': 0})})

    correios_SiteID = fields.Char(string="Correios SiteID")
    correios_password = fields.Char(string="Correios Password")
    correios_account_number = fields.Char(string="Correios Account Number")
    correios_cartaopostagem = fields.Char(string="Correios Cartão Postagem")
    correios_codigoServicio = fields.Char(string="Correios Código de Servicio")

    correios_access_token = fields.Text(
        string="Correios Access Token",
        help="Generate access token using Correios credentials", copy=False
    )
    correios_token_valid_upto = fields.Datetime(
        string="Token Expiry", copy=False,
        help="Correios token expires in 10 days. Token will be auto generate based on this token expiry date."
    )
    correios_default_package_type_id = fields.Many2one(
        "stock.package.type",
        string="Package Type",
        help="Correios requires package dimensions for getting accurate rate, "
             "you can define these in a package type that you set as default"
    )

    modalidade_frete = fields.Selection(
        [('0', '0 - Contratação do Frete por conta do Remetente (CIF)'),
         ('1', '1 - Contratação do Frete por conta do Destinatário (FOB)'),
         ('2', '2 - Contratação do Frete por conta de Terceiros'),
         ('3', '3 - Transporte Próprio por conta do Remetente'),
         ('4', '4 - Transporte Próprio por conta do Destinatário'),
         ('9', '9 - Sem Ocorrência de Transporte')], 
        required=True,
        company_dependent=True, 
        string="Default Fleet mode", 
        help="Default fleet mode method used in sales orders.")


    @api.ondelete(at_uninstall=False)
    def _unlink_except_commercial_invoice_sequence(self):
        if self.env.ref('delivery_correios.correios_commercial_invoice_seq').id in self.ids:
            raise UserError(_('You cannot delete the commercial invoice sequence.'))

    def _compute_can_generate_return(self):
        super(Providercorreios, self)._compute_can_generate_return()
        for carrier in self:
            if carrier.delivery_type == 'correios':
                carrier.can_generate_return = True

    def _compute_supports_shipping_insurance(self):
        super(Providercorreios, self)._compute_supports_shipping_insurance()
        for carrier in self:
            if carrier.delivery_type == 'correios':
                carrier.supports_shipping_insurance = True

    def correios_rate_shipment(self, order):
        """
        Returns shipping rate for the order and chosen shipping method.
        """
        sr = CorreiosProvider(self, self.log_xml)
        result = sr._rate_request(
            order.partner_shipping_id,
            order.warehouse_id.partner_id or order.warehouse_id.company_id.partner_id,
            order
        )
        if result.get('error_found'):
            return {'success': False, 'price': 0.0, 'error_message': result['error_found'], 'warning_message': False}
        price = float(result.get('pcFinal').replace('.','*').replace(',','.').replace('*',','))
        if order.currency_id.id != self.env.ref('base.BRL').id:
            price = self._correios_converted_amount(order, price)
        return {
            'success': True,
            'price': price,
            'error_message': False,
            'warning_message': result.get('warning_message')
        }

    def _correios_converted_amount(self, order, price_brl):
        """
        Returns the converted amount from the INR amount based on order's currency.
        """
        return self.env.ref('base.BRL')._convert(
            price_brl,
            order.currency_id,
            order.company_id,
            fields.Date.context_today(self)
        )

    def _rate_shipment_vals(self, order=False, picking=False):
        if picking:
            warehouse_partner_id = picking.picking_type_id.warehouse_id.partner_id
            currency_id = picking.sale_id.currency_id or picking.company_id.currency_id
            destination_partner_id = picking.partner_id
            total_value = sum(sml.sale_price for sml in picking.move_line_ids)
        else:
            warehouse_partner_id = order.warehouse_id.partner_id
            currency_id = order.currency_id or order.company_id.currency_id
            total_value = sum(line.price_reduce_taxinc * line.product_uom_qty for line in order.order_line.filtered(lambda l: l.product_id.type in ('consu', 'product') and not l.display_type))
            destination_partner_id = order.partner_shipping_id

        rating_request = {}
        srm = CorreiosProvider(self, logger=self.log_xml, prod_environment=self.prod_environment)
        cep_adddress = srm._get_address_cep(warehouse_partner_id)
        return False
    

        # check_value = srm.check_required_value(self, destination_partner_id, warehouse_partner_id, order=order, picking=picking)

    def _rate_shipment_vals_XXX(self, order=False, picking=False):
        if picking:
            warehouse_partner_id = picking.picking_type_id.warehouse_id.partner_id
            currency_id = picking.sale_id.currency_id or picking.company_id.currency_id
            destination_partner_id = picking.partner_id
            total_value = sum(sml.sale_price for sml in picking.move_line_ids)
        else:
            warehouse_partner_id = order.warehouse_id.partner_id
            currency_id = order.currency_id or order.company_id.currency_id
            total_value = sum(line.price_reduce_taxinc * line.product_uom_qty for line in order.order_line.filtered(lambda l: l.product_id.type in ('consu', 'product') and not l.display_type))
            destination_partner_id = order.partner_shipping_id

        rating_request = {}
        srm = CorreiosProvider(self.log_xml, request_type="rate", prod_environment=self.prod_environment)
        check_value = srm.check_required_value(self, destination_partner_id, warehouse_partner_id, order=order, picking=picking)
        if check_value:
            return {'success': False,
                    'price': 0.0,
                    'error_message': check_value,
                    'warning_message': False}
        site_id = self.sudo().correios_SiteID
        password = self.sudo().correios_password
        rating_request['Request'] = srm._set_request(site_id, password)
        rating_request['From'] = srm._set_dct_from(warehouse_partner_id)
        if picking:
            packages = self._get_packages_from_picking(picking, self.correios_default_package_type_id)
        else:
            packages = self._get_packages_from_order(order, self.correios_default_package_type_id)
        rating_request['BkgDetails'] = srm._set_dct_bkg_details(self, packages)
        rating_request['To'] = srm._set_dct_to(destination_partner_id)
        if self.correios_dutiable:
            rating_request['Dutiable'] = srm._set_dct_dutiable(total_value, currency_id.name)
        real_rating_request = {}
        real_rating_request['GetQuote'] = rating_request
        real_rating_request['schemaVersion'] = 2.0
        self._correios_add_custom_data_to_request(rating_request, 'rate')
        response = srm._process_rating(real_rating_request)

        available_product_code = []
        shipping_charge = False
        qtd_shp = response.findall('GetQuoteResponse/BkgDetails/QtdShp')
        if qtd_shp:
            for q in qtd_shp:
                charge = q.find('ShippingCharge').text
                global_product_code = q.find('GlobalProductCode').text
                if global_product_code == self.correios_product_code and charge:
                    shipping_charge = charge
                    shipping_currency = q.find('CurrencyCode')
                    shipping_currency = None if shipping_currency is None else shipping_currency.text
                    break
                else:
                    available_product_code.append(global_product_code)
        else:
            condition = response.find('GetQuoteResponse/Note/Condition')
            if condition:
                condition_code = condition.find('ConditionCode').text
                if condition_code == '410301':
                    return {
                        'success': False,
                        'price': 0.0,
                        'error_message': "%s.\n%s" % (condition.find('ConditionData').text, _("Hint: The destination may not require the dutiable option.")),
                        'warning_message': False,
                    }
                elif condition_code in ['420504', '420505', '420506', '410304'] or\
                        response.find('GetQuoteResponse/Note/ActionStatus').text == "Failure":
                    return {
                        'success': False,
                        'price': 0.0,
                        'error_message': "%s." % (condition.find('ConditionData').text),
                        'warning_message': False,
                    }
        if shipping_charge:
            if order:
                order_currency = order.currency_id
            else:
                order_currency = picking.sale_id.currency_id or picking.company_id.currency_id
            if shipping_currency is None or order_currency.name == shipping_currency:
                price = float(shipping_charge)
            else:
                quote_currency = self.env['res.currency'].search([('name', '=', shipping_currency)], limit=1)
                price = quote_currency._convert(float(shipping_charge), order_currency, (order or picking).company_id, order.date_order if order else fields.Date.today())
            return {'success': True,
                    'price': price,
                    'error_message': False,
                    'warning_message': False}

        if available_product_code:
            return {'success': False,
                    'price': 0.0,
                    'error_message': _(
                        "There is no price available for this shipping, you should rather try with the Correios product %s",
                        available_product_code[0]),
                    'warning_message': False}

    def correios_send_shipping(self, pickings):
        """
        Send shipment to correios. Once the correios order is
        generated, it will post the message(s) with tracking link,
        shipping label pdf and manifest pdf.
        """

        sr = CorreiosProvider(self, self.log_xml)

        def _get_document_data(url):
            """ Returns the document content for label and manifest. """
            document_response = sr._make_api_request(url, timeout=30)
            return document_response

        res = []
        for picking in pickings:
            
            shippings = sr._send_shipping(picking)
            picking.correios_orders = " + ".join(shippings.get('order_ids'))
            res.append({
                'tracking_number': " + ".join(shippings.get('tracking_numbers')),
                'exact_price': shippings.get('exact_price')
            })
            carrier_tracking_numbers = []
            for pack in shippings['all_pack'].values():
                response = pack.get('response')
                courier_name = response.get('courier_name')
                carrier_tracking_numbers += shippings.get('tracking_numbers', [])
                # carrier_tracking_ref = " + ".join(shippings.get('tracking_numbers') + [carrier_tracking_ref])
                carrier_tracking_ref = response.get('tracking_numbers')
                if response.get('warning_message'):
                    picking.message_post(body='%s' % (response['warning_message']))
                if response.get('label_url'):
                    label_data = _get_document_data(response['label_url'])
                    attachments = [("%s-%s.pdf" % (courier_name, carrier_tracking_ref), label_data)]
                    log_message = _("Label generated of %s with Tracking Number: %s",
                                    courier_name, carrier_tracking_ref)
                    picking.message_post(body=log_message, attachments=attachments)
                # if correios_pickup_request is enable then only correios generate manifest(s).
                # if self.correios_manifests_generate and response.get('manifest_url'):
                #     manifest_data = _get_document_data(response['manifest_url'])
                #     attachments = [("Manifest - %s-%s.pdf" % (courier_name, carrier_tracking_ref), manifest_data)]
                #     log_message = _("Manifest generated of %s", courier_name)
                #     picking.message_post(body=log_message, attachments=attachments)
            # when carrier is in test mode, need to cancel correios order.
            picking.carrier_tracking_ref = " + ".join(carrier_tracking_numbers)
            # if not self.prod_environment:
            #     # Need carrier_tracking_ref to cancel shipment
            #     # picking.carrier_tracking_ref = " + ".join(shippings.get('tracking_numbers'))
            #     self.correios_cancel_shipment(picking)
        return res

    def correios_get_return_label(self, pickings, tracking_number=None, origin_date=None):
        sr = CorreiosProvider(self, self.log_xml)
        objetos = [pick.carrier_tracking_ref for pick in pickings]
        site_id = self.sudo().correios_SiteID
        postagem = {
            'codigosObjeto': objetos,
            # [
            #         picking.carrier_tracking_ref,
            # ],
            'idCorreios': site_id,
            'numeroCartaoPostagem': self.sudo().correios_cartaopostagem,
            'tipoRotulo': 'P',
            'formatoRotulo': 'ET',
            'imprimeRemetente': 'S',
            # "idsPrePostagem": [
            #     "PRWVaNM2zsQmG9LAfADkpLNg"
            # ],
            'layoutImpressao': 'PADRAO'
        }   

        response_json = sr._make_api_request('/prepostagem/v1/prepostagens/rotulo/assincrono/pdf', 'POST', data=postagem, token=sr._get_token())
        data = {}
        time.sleep(1)
        idRecibo = response_json.get('idRecibo')
        filecontent = ''
        cont = 0
        while not filecontent:
            response_label = sr._make_api_request('/prepostagem/v1/prepostagens/rotulo/download/assincrono/%s' % idRecibo, 'GET', token=sr._get_token())
            # filecontent = base64.b64decode(response_label.get('dados') or '')
            filecontent = response_label.get('dados') or ''
            cont += 1
            if cont >= 5:
                break

        base_url = self.env['ir.config_parameter'].get_param('web.base.url')
        attachment_obj = self.env['ir.attachment']
        # create attachment
        attachment_id = attachment_obj.create(
            {'name': response_label.get('nome'), 'datas': filecontent})
        # prepare download url
        download_url = '/web/content/' + str(attachment_id.id) + '?download=true'
        # download
        return {
            "type": "ir.actions.act_url",
            "url": str(base_url) + str(download_url),
            "target": "new",
        }

    def correios_get_tracking_link(self, picking):
        return 'https://rastreamento.correios.com.br/app/index.php?objetos=%s' % picking.carrier_tracking_ref
    
    def correios_cancel_shipment(self, picking):
        # Obviously you need a pick up date to delete SHIPMENT by Correios. So you can't do it if you didn't schedule a pick-up.
        picking.message_post(body=_(u"You can't cancel Correios shipping without pickup date."))
        picking.write({'carrier_tracking_ref': '',
                       'carrier_price': 0.0})

    def _correios_convert_weight(self, weight):
        """
        Returns the weight in g for a correios order.
        """
        self.ensure_one()
        weight_uom_id = self.env['product.template']._get_weight_uom_id_from_ir_config_parameter()
        return weight_uom_id._compute_quantity(weight, self.env.ref('uom.product_uom_gram'), round=False)

    # def _correios_convert_weight(self, weight, unit):
    #     weight_uom_id = self.env['product.template']._get_weight_uom_id_from_ir_config_parameter()
    #     if unit == 'L':
    #         weight = weight_uom_id._compute_quantity(weight, self.env.ref('uom.product_uom_lb'), round=False)
    #     else:
    #         weight = weight_uom_id._compute_quantity(weight, self.env.ref('uom.product_uom_kgm'), round=False)
    #     return float_repr(weight, 3)

    def _correios_add_custom_data_to_request(self, request, request_type):
        """Adds the custom data to the request.
        When there are multiple items in a list, they will all be affected by
        the change.
        for example, with
        {"ShipmentDetails": {"Pieces": {"Piece": {"AdditionalInformation": "custom info"}}}}
        the AdditionalInformation of each piece will be updated.
        """
        if not self.correios_custom_data_request:
            return
        try:
            custom_data = const_eval('{%s}' % self.correios_custom_data_request).get(request_type, {})
        except SyntaxError:
            raise UserError(_('Invalid syntax for Correios custom data.'))

        def extra_data_to_request(request, custom_data):
            """recursive function that adds custom data to the current request."""
            for key, new_value in custom_data.items():
                request[key] = current_value = serialize_object(request.get(key, {})) or None
                if isinstance(current_value, list):
                    for item in current_value:
                        extra_data_to_request(item, new_value)
                elif isinstance(new_value, dict) and isinstance(current_value, dict):
                    extra_data_to_request(current_value, new_value)
                else:
                    request[key] = new_value

        extra_data_to_request(request, custom_data)

    def _correios_calculate_value(self, picking):
        sale_order = picking.sale_id
        if sale_order:
            total_value = sum(line.price_reduce_taxinc * line.product_uom_qty for line in
                              sale_order.order_line.filtered(
                                  lambda l: l.product_id.type in ('consu', 'product') and not l.display_type))
            currency_name = picking.sale_id.currency_id.name
        else:
            total_value = sum([line.product_id.lst_price * line.product_qty for line in picking.move_ids])
            currency_name = picking.company_id.currency_id.name
        return total_value, currency_name

    def postagem_control(self, picks=None):
        if not picks:
            raise ValueError(_('Not picking to post'))
        postagem = {}
        for move in picks.mapped('move_ids'):

            default_package = self.correios_default_package_type_id
            packages = self._get_packages_from_picking(move.picking_id, default_package)

            dimension = {}
            if len(packages) == 1:
                dimension = {
                    'length': packages[0].dimension['length'],
                    'width': packages[0].dimension['width'],
                    'height': packages[0].dimension['height']
                }
            
            postagem.update({
                "ncmObjeto": move.product_id.l10n_br_ncm_id.code,
                # "codigoObjeto": move.product_id.default_code,
                "pesoInformado": "300",
                "codigoFormatoObjetoInformado": "2",
                "alturaInformada": dimension['height'],
                "larguraInformada": dimension['width'],
                "comprimentoInformado": dimension['length'],
            })

        site_id = self.sudo().correios_SiteID

        # Get package sender
        vendor_id = picks.location_id.warehouse_id.partner_id
        if all(picks.mapped('move_id.sale_line_id.order_id.is_client_sender')):
            vendor_id = picks.mapped('move_id.sale_line_id.order_id.partner_id')[0]

        postagem.update({
            "idCorreios": site_id,
            "remetente": self._prepare_vendor_details(vendor_id),
            "destinatario": self._prepare_client_details(picks.partner_id),
            "codigoServico": self.correios_codigoServicio,
            # "precoServico": "string",
            # "precoPrePostagem": "string",
            # "numeroNotaFiscal": "string",
            # "numeroCartaoPostagem": "string",
            # "chaveNFe": "string",
            # "listaServicoAdicional": [
                # {
                # "codigoServicoAdicional": "string",
                # "tipoServicoAdicional": "string",
                # "nomeServicoAdicional": "string",
                # "valorServicoAdicional": "string",
                # "valorDeclarado": "string",
                # "siglaServicoAdicional": "string",
                # "orientacaoEntregaVizinho": "string",
                # "tipoChecklist": "str",
                # "subitensCheckList": [
                #     {
                #     "codigo": "string"
                #     }
                # ]
                # }
            # ],
            # "itensDeclaracaoConteudo": [
            #     {
            #     "conteudo": "string",
            #     "quantidade": "string",
            #     "valor": "string"
            #     }
            # ],
            # "rfidObjeto": "string",
            "cienteObjetoNaoProibido": "1",
            # "idAtendimento": "string",
            "solicitarColeta": "S",
            "dataPrevistaPostagem": fields.Date.today().strftime('%d/%m/%Y'),
            # "observacao": "string",
            "modalidadePagamento": "2",
            "logisticaReversa": "N",
            # "dataValidadeLogReversa": "01/01/2021 para primeiro de janeiro de 2021 (dd/MM/yyyy)"

        })

        sr = CorreiosProvider(self, self.log_xml)


        response_json = sr._make_api_request('prepostagem/v1/prepostagens', 'POST', data=postagem, token=sr._get_token())

        return response_json

    def _parse_br_phonenumber(self, phone):
        try:
            phonenumber = phonenumbers.parse(phone, "BR")
        except phonenumbers.phonenumberutil.NumberParseException as e:
            raise ValidationError(_(f"Unable to parse {phone}: {str(e)}")) from e
        
        return {
            'country_code': str(phonenumber.country_code),
            'area_code': str(phonenumber.national_number)[:2],
            'local_number': str(phonenumber.national_number)[2:],
        }
    
    def _prepare_vendor_details(self, vendor_id):
        telefone = self._parse_br_phonenumber(vendor_id.phone)
        # telefone = vendor_id.phone and vendor_id.phone.split(')')[1] or ''
        celular = self._parse_br_phonenumber(vendor_id.mobile)
        # celular = vendor_id.mobile and vendor_id.mobile.split(')')[1] or ''
            
        return {
            "nome": vendor_id.name,
            "dddTelefone": telefone['area_code'],
            "telefone": telefone['local_number'],
            "dddCelular": celular['area_code'],
            "celular": celular['local_number'],
            "email": vendor_id.email,
            "cpfCnpj": vendor_id.vat,
            # "documentoEstrangeiro": "string",
            # "obs": "string",
            "endereco": {
            "cep": vendor_id.zip.replace('-', ''),
            "logradouro": vendor_id.street,
            "numero": vendor_id.street,
            "complemento": vendor_id.street2,
            # "bairro": "string",
            "cidade": vendor_id.city,
            "uf": vendor_id.state_id.code,
            # "pais": vendor_id.country_id.name,
            }
        }

    def _prepare_client_details(self, client_id):
        telefone = self._parse_br_phonenumber(client_id.phone)
        # telefone = vendor_id.phone and vendor_id.phone.split(')')[1] or ''
        celular = self._parse_br_phonenumber(client_id.mobile)
        # celular = vendor_id.mobile and vendor_id.mobile.split(')')[1] or ''
            
        return {
            "nome": client_id.name,
            "dddTelefone": telefone['area_code'],
            "telefone": telefone['local_number'],
            "dddCelular": celular['area_code'],
            "celular": celular['local_number'],
            "email": client_id.email,
            "cpfCnpj": client_id.vat,
            # "documentoEstrangeiro": "string",
            # "obs": "string",
            "endereco": {
            "cep": client_id.zip.replace('-', ''),
            "logradouro": client_id.street,
            "numero": client_id.street,
            "complemento": client_id.street2,
            # "bairro": "string",
            "cidade": client_id.city,
            "uf": client_id.state_id.code,
            "regiao": "string",
            # "pais": client_id.country_id.name
            },
  }

