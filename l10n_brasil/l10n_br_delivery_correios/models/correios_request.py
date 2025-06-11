# -*- coding: utf-8 -*-

import json
import logging
import re
import requests
from odoo.addons.phone_validation.tools import phone_validation


from requests.exceptions import RequestException
from werkzeug.urls import url_join
from collections import defaultdict
from datetime import datetime, timedelta

from odoo import fields, _
from odoo.exceptions import UserError, ValidationError, RedirectWarning
from odoo.tools import format_date

_logger = logging.getLogger(__name__)

class CorreiosProvider:

    def __init__(self, carrier, logger=None, prod_environment=False):
        """
        Initial parameters for making api requests.
        """
        self.carrier = carrier
        self.logger = logger
        self.url = 'https://api.correios.com.br/'
        if prod_environment:
            self.url = 'https://api.correios.com.br/'

        self.session = requests.Session()
        # self.session.headers = {
        #     'Content-Type': 'application/json',
        #     'StarShipIT-Api-Key': api_key,
        #     'Ocp-Apim-Subscription-Key': subscription_key,
        # }

    def _make_api_request(self, endpoint, method='GET', auth=None, data=None, token=None):
        """
        make an api call, return response for multiple api requests of correios
        """
        headers = {'Content-Type': 'application/json'}

        if not auth:
            auth = ('22470453000114', 'leZrGkelOpr6bfi9HoWwWliHWIyEsH7LjB6odFDC')
        if token:
            # Include authorization header if a token is provided
            headers['Authorization'] = 'Bearer {}' .format(token)
            auth = None

        # elif 'Authorization' in data:
        #     headers.update({'Authorization': data.pop('Authorization'),
        #                     'accept': 'application/json'})
        access_url = endpoint
        if endpoint[:4] != 'http':
            access_url = url_join(self.url, endpoint)
        
        try:
            # Log the request details for debugging purposes
            if self.logger:
                self.logger("%s\n%s\n%s" % (access_url, method, data), 'correios_request_%s' % endpoint)
            # Make the API request
            response = self.session.request(method, access_url, auth=auth, json=data, headers=headers, timeout=30)
            # Parse the response as JSON
            response_json = response.json()
            # Log the response details for debugging purposes
            if self.logger:
                self.logger("%s\n%s" % (response.status_code, response.text), 'correios_response_%s' % endpoint)
            if not response.status_code in range(200, 205):
                response_json = {'errors': response_json}
            return response_json
        except requests.exceptions.ConnectionError as error:
            _logger.warning('Connection Error: %s with the given URL: %s', error, access_url)
            return {'errors': {'timeout': "Cannot reach the server. Please try again later."}}
        except json.decoder.JSONDecodeError as error:
            _logger.warning('JSONDecodeError: %s', error)
            return {'errors': {'JSONDecodeError': str(error)}}

    def _authorize_generate_token(self):
        """
        Generate an access token from correios credentials.
        """
        # auth = (self.carrier.correios_SiteID, self.carrier.correios_password)
        data = {'numero': self.carrier.sudo().correios_cartaopostagem,
                'contrato': self.carrier.sudo().correios_account_number,
                # 'Authorization': 'Basic {}' .format(self.carrier.correios_password),
                }
        # data = {'Authorization': 'Basic {}' .format(self.carrier.correios_password),
        #         }
        # data = {}
        auth = (self.carrier.sudo().correios_SiteID, self.carrier.sudo().correios_password)

        return self._make_api_request('token/v1/autentica/cartaopostagem', 'POST', auth=auth, data=data)

    def _get_token(self):
        """
        Generate an access token for correios.
        The token is automatically generates after 9 days as it expires.
        """
        if not (self.carrier.sudo().correios_SiteID and self.carrier.sudo().correios_password):
            action = self.carrier.env.ref('delivery.action_delivery_carrier_form')
            raise RedirectWarning(
                _("Please configure correios credentials in the shipping method"), action.id,
                _("Go to Shipping Method")
            )
        if not self.carrier.correios_access_token or (
            self.carrier.correios_token_valid_upto and
            self.carrier.correios_token_valid_upto < fields.datetime.today()
        ):
            response_json = self._authorize_generate_token()
            if 'token' in response_json:
                self.carrier.sudo().write({
                    'correios_access_token': response_json['token'],
                    'correios_token_valid_upto': datetime.strptime(response_json['expiraEm'],"%Y-%m-%dT%H:%M:%S"),
                })
        return self.carrier.correios_access_token

    def _get_address_cep(self, partner_zip):

        return self._make_api_request('cep/v1/enderecos/{}'.format(partner_zip.replace('-', '')), token=self._get_token())

    def _get_rate(self, shipper, recipient, weight_in_g, dimensions):
        """
        Fetch rate from correios API based on the parameters.
        """
        res = {'currency': 'BRL'}
        is_brasil = recipient.country_id.code == 'BR' and shipper.country_id.code == 'BR'
        data = {
            'cepOrigem': re.sub(r'\D', '', shipper.zip),
            'cepDestino': re.sub(r'\D', '', recipient.zip),
            'psObjeto': '{:.0f}'.format(weight_in_g),
            'tpObjeto': '2',     # 1.Envelope, 2.Pacote, 3.Rolo
            'comprimento': '{:.0f}'.format(dimensions.get('length') * 100),
            'largura': '{:.0f}'.format(dimensions.get('width') * 100),
            'altura': '{:.0f}'.format(dimensions.get('height') * 100),
         }
        params = '&'.join(['{}={}'.format(k, v) for k, v in data.items()])
        
        if is_brasil:
            rate_json = self._make_api_request('preco/v1/nacional/03220?{}'.format(params), token=self._get_token())
        else:
            data['cod'] = 0
            rate_json = self._make_api_request('external/courier/international/serviceability', data=data, token=self._get_token())
        if rate_json and rate_json.get('pcFinal'):
            res = rate_json
        else:
            res.update({'error_found': self._correios_get_error_message(rate_json)})
        return res

    def _prepare_parcel(self, picking, package, courier_code=False, ship_charges=0.00, index=1):
        """
        Prepare parcel for picking shipment based on the package.
        """
        database_uuid = picking.env['ir.config_parameter'].sudo().get_param('database.uuid')
        unique_ref = str(index) + '-' + database_uuid[:5]
        order_name = picking.name
        partner = partner_invoice = picking.partner_id
        if partner.child_ids.filtered(lambda p: p.type == 'invoice'):
            partner_invoice = partner.child_ids.filtered(lambda p: p.type == 'invoice')[0]
        warehouse_partner_id = picking.picking_type_id.warehouse_id.partner_id or picking.company_id.partner_id
        warehouse_partner_name = re.sub(r'[^a-zA-Z0-9\s]+', '', warehouse_partner_id.name)[:36]
        if picking.sale_id:
            partner_invoice = picking.sale_id.partner_invoice_id
            order_name = order_name + '-' + picking.sale_id.name
        dimensions = package.dimension
        net_weight_in_g = self.carrier._correios_convert_weight(package.weight)
        line_vals = self._get_shipping_lines(package, picking).values()
        # Payment method not needed
        # payment_method = "Prepaid" if self.carrier.correios_payment_method == "prepaid" else "COD"
        
        # Get package sender
        vendor_id = warehouse_partner_id
        if all(picking.mapped('move_ids.sale_line_id.order_id.is_client_sender')):
            vendor_id = picking.mapped('move_ids.sale_line_id.order_id.partner_id')[0]


        remetente_values = self._prepare_partner_details(vendor_id)
        destinatario_values = self._prepare_partner_details(partner)

        site_id = picking.sudo().carrier_id.correios_SiteID
        # retornando valor
        return {
            "idCorreios": site_id,
            "remetente": remetente_values,
            "destinatario": destinatario_values,
            "codigoServico": picking.carrier_id.correios_codigoServicio,
            ########### add object ncm "ncmObjeto": move.product_id.l10n_br_ncm_id.code,
            # "codigoObjeto": move.product_id.default_code,
            "pesoInformado": "{:.0f}".format(net_weight_in_g),
            "codigoFormatoObjetoInformado": "2",
            "comprimentoInformado": dimensions.get('length') * 100,
            "larguraInformada": dimensions.get('width') * 100,
            "alturaInformada": dimensions.get('height') * 100,
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

        }

    def _prepare_vendor_details(self, vendor_id):

        dddTelefone = ''
        telefone = ''
        if vendor_id.phone and vendor_id.phone[:3] == '+55':
            dddTelefone = vendor_id.phone[4:6]
            telefone = vendor_id.phone.replace(' ', '').replace('-', '')[5:]

        dddCelular = ''
        celular = ''
        if vendor_id.mobile and vendor_id.mobile[:3] == '+55':
            dddCelular = vendor_id.mobile[4:6]
            celular = vendor_id.mobile.replace(' ', '').replace('-', '')[5:]
            
        data = {
            "nome": vendor_id.name,
            "dddTelefone": dddTelefone,
            "telefone": telefone,
            "dddCelular": dddCelular,
            "celular": celular,
            "email": vendor_id.email,
            "cpfCnpj": vendor_id.vat,
            # "documentoEstrangeiro": "string",
            # "obs": "string",
            "endereco": {
            "cep": vendor_id.zip.replace('-', ''),
            "logradouro": vendor_id.street,
            "numero": vendor_id.street,
            "complemento": vendor_id.street2,
            "bairro": vendor_id.district,
            "cidade": vendor_id.city_id and vendor_id.city_id.name or vendor_id.city or '',
            "uf": vendor_id.state_id.code,
            # "pais": vendor_id.country_id.name,
            }
        }

        if vendor_id.street2:
            data['endereco'].update({"complemento": vendor_id.street2})
        if vendor_id.country_enforce_cities and vendor_id.city_id:
            data['endereco'].update({"cidade": vendor_id.city_id.name})
        return data

    def _prepare_partner_details(self, partner_id):
        # Telefone Format
        dddTelefone = ''
        telefone = ''
        if partner_id.phone and partner_id.phone[:3] == '+55':
            dddTelefone = partner_id.phone[4:6]
            telefone = partner_id.phone.replace(' ', '').replace('-', '')[5:]

        # Mobile Format
        dddCelular = ''
        celular = ''
        if partner_id.mobile and partner_id.mobile[:3] == '+55':
            dddCelular = partner_id.mobile[4:6]
            celular = partner_id.mobile.replace(' ', '').replace('-', '')[5:]
            
        # Address Format
        numero = partner_id.street and partner_id.street.split(' ')[-1] or ''
        logradouro = partner_id.street and partner_id.street.replace(numero, '').strip() or ''
            
        data = {
            "nome": partner_id.name,
            "dddTelefone": dddTelefone,
            # "ddiTelefone": "str",
            "telefone": telefone,
            "dddCelular": dddCelular,
            # "ddiCelular": "str",
            "celular": celular,
            "email": partner_id.email,
            "cpfCnpj": partner_id.vat,
            # "documentoEstrangeiro": "string",
            # "obs": "string",
            "endereco": {
            "cep": partner_id.zip.replace('-', ''),
            "logradouro": logradouro,
            "numero": numero,
            "bairro": partner_id.district,
            "cidade": partner_id.city_id and partner_id.city_id.name or partner_id.city or '',
            "uf": partner_id.state_id.code,
            "regiao": "string",
            # "pais": partner_id.country_id.name
            },
        }
        if partner_id.street2:
            data['endereco'].update({"complemento": partner_id.street2})
        if partner_id.country_enforce_cities and partner_id.city_id:
            data['endereco'].update({"cidade": partner_id.city_id.name})
        return data

    def _get_shipping_params(self, picking):
        """
        Returns the shipping data from picking for create an correios order.
        """
        if not self.carrier.correios_codigoServicio:
            action = self.carrier.env.ref('delivery.action_delivery_carrier_form')
            raise RedirectWarning(_("Configure Correios Service in shipping method"), action.id,
                                  _("Go to Shipping Methods"))
        parcel_dict = {}
        ship_from = picking.picking_type_id.warehouse_id.partner_id or picking.company_id.partner_id
        default_package = self.carrier.correios_default_package_type_id
        packages = self.carrier._get_packages_from_picking(picking, default_package)
        for index, package in enumerate(packages):
            # fetch rate based on package weight and size
            rate_response = self._rate_request(picking.partner_id, ship_from, picking=picking, package=package)
            # need courier code, as the rate request and forward shipment APIs must have to use the same courier code.
            if 'error_found' in rate_response:
                raise ValidationError(rate_response['error_found'])
            courier_code = rate_response.get('courier_code')
            # ship_charges is mandatory as forward shipment API will not return it in response, used in subtotal.
            ship_charges = float(rate_response.get('pcFinal').replace('.','*').replace(',','.').replace('*',','))
            # ship_charges = rate_response.get('price')
            parcel = self._prepare_parcel(picking, package, courier_code, ship_charges, index=index)
            parcel_dict[package] = parcel
        return parcel_dict

    def _send_shipping(self, picking):
        """
        Returns the dictionary with order_id, shipment_id, tracking_number,
        exact_price and courier_name for delivery order.
        - for multiple package, correios create new order.
        https://apiv2.correios.in/v1/external/shipments/create/forward-shipment
        """
        products = picking.move_line_ids.product_id
        self._check_required_value(
            picking.partner_id,
            picking.picking_type_id.warehouse_id.partner_id or picking.company_id.partner_id,
            products and products.filtered(lambda p: p.detailed_type in ['consu', 'product'])
        )
        res = {
            'exact_price': 0.00,
            'tracking_numbers': [],
            'order_ids': [],
            'all_pack': defaultdict(lambda: {'response': {}, 'order_details': {}})
        }
        params = self._get_shipping_params(picking)
        for delivery_package, correios_parcel in params.items():
            payload = self._make_api_request('prepostagem/v1/prepostagens', 'POST', 
                                               data=correios_parcel, 
                                               token=self._get_token()
            )
            # _logger.warning('CrossPy - data: %s' % correios_parcel)
            if payload.get('errors'):
                picking.message_post(body=self._correios_get_error_message(payload))
                continue
            # payload = order_response.get('id')
            if not payload:
                picking.message_post(body=_('AWB assignment was unsuccessful: %s') % (self._correios_get_error_message(payload)))
                continue
            if not payload or payload.get('errors'):
                raise ValidationError(_('Erro no processo de registro no correios'))
            res['all_pack'][delivery_package]['response'] = payload
            if payload.get('codigoObjeto') and payload.get('error_message') and 'Oops! Cannot reassign courier' in payload['error_message']:
                payload.pop('error_message')
                res['all_pack'][delivery_package]['response']['warning_message'] = \
                    _("Same order is available in correios so label provided is the copy of existing one.")
                label_response = self._generate_label(payload['shipment_id'])
                res['all_pack'][delivery_package]['response']['label_url'] = label_response.get('label_url')
            if payload.get('codigoObjeto') and not payload.get('error_message') and not payload.get('awb_assign_error'):
                res['tracking_numbers'].append(payload.get('codigoObjeto'))
                res['order_ids'].append(payload.get('id'))
                # To get exact_price
                # order_details = self._make_api_request('external/shipments/{}'.format(payload['shipment_id']), token=self._get_token())
                # order_id = order_details.get('data').get('order_id')
                # if order_id:
                #     res['order_ids'].append(str(order_id))
                # res['all_pack'][delivery_package]['order_details'] = order_details
                # res['exact_price'] += float(order_details.get('data', {}).get('charges', {}).get('freight_charges', '0.00'))
            else:
                picking.message_post(body=_('AWB assignment was unsuccessful: %s') % (self._correios_get_error_message(payload)))
        # _logger.warning('CrossPy - _send_shipping.res: %s' % res)
        return res

    def _generate_label(self, shipment_id):
        """
        Generate Label for correios order if the forward shipment fails
        to generate label again and shipment is already created in correios.
        https://apiv2.correios.in/v1/external/courier/generate/label
        """
        label_data = {"shipment_id": [shipment_id]}
        label_result = self._make_api_request(
            'external/courier/generate/label',
            'POST',
            label_data,
            token=self._get_token()
        )
        if label_result and 'label_url' in label_result:
            return label_result
        raise ValidationError(self._correios_get_error_message(label_result))

    def _send_cancelling(self, orders_data, pickup_request):
        """
        Cancelling correios order/shipment.
        https://apiv2.correios.in/v1/external/orders/cancel
        https://apiv2.correios.in/v1/external/orders/cancel/shipment/awbs
        """
        cancel_result = {}
        for order in orders_data:
            if pickup_request:
                endpoint = 'external/orders/cancel'
                data = {'ids': [order]}
            else:
                endpoint = 'external/orders/cancel/shipment/awbs'
                data = {'awbs': [order]}
            cancel_result.update({
                order: self._make_api_request(endpoint, 'POST', data, token=self._get_token()
            )})
        return cancel_result

    def _check_required_value(self, recipient, shipper, products):
        """
        Check if the required value are not present in order to process an API call.
        return True or return an error if configuration is missing.
        """
        customer=_('Customer')
        shipper_=_('Shipper')
        error_msg = {customer: [], shipper_: []}
        if not recipient.street:
            error_msg[customer].append(_("Street is required!"))
        if not recipient.zip:
            error_msg[customer].append(_("Zip is required!"))
        if not recipient.country_id:
            error_msg[customer].append(_("Country is required!"))
        if not recipient.email:
            error_msg[customer].append(_("Email is required!"))
        if not recipient.phone and not recipient.mobile:
            error_msg[customer].append(_("Phone or Mobile is required!"))
        if not shipper.street:
            error_msg[shipper_].append(_("Street is required!"))
        if not shipper.zip:
            error_msg[shipper_].append(_("Zip is required!"))
        if not shipper.country_id:
            error_msg[shipper_].append(_("Country is required!"))
        if not shipper.email:
            error_msg[shipper_].append(_("Email is required!"))
        if not shipper.phone and not shipper.mobile:
            error_msg[shipper_].append(_("Phone or Mobile is required!"))
        for product in products:
            if not product.weight:
                error_msg.setdefault(product.name, [])
                error_msg[product.name].append(_("Weight is missing!"))
            if not product.default_code:
                error_msg.setdefault(product.name, [])
                error_msg[product.name].append(_("SKU is missing!"))
        if error_msg:
            msg = "".join(e_for + "\n- " + "\n- ".join(e) + "\n" for e_for, e in error_msg.items() if e)
            if msg:
                raise ValidationError(msg)
        return True


    def _rate_request(self, recipient, shipper, order=False, picking=False, package=False):
        """
        Returns the dictionary of shipment rate from correios
        https://apiv2.correios.in/v1/external/courier/serviceability/
        https://apiv2.correios.in/v1/external/courier/international/serviceability
        """
        if not (order or picking):
            raise UserError(_('Sale Order or Picking is required to get rate.'))
        products = picking and picking.move_ids.product_id or order.order_line.product_id
        self._check_required_value(
            recipient, shipper,
            products and products.filtered(lambda p: p.detailed_type in ['consu', 'product'])
        )
        if package:
            total_weight = package.weight
            dimensions = package.dimension
        else:
            default_package = self.carrier.correios_default_package_type_id
            if picking:
                packages = self.carrier._get_packages_from_picking(picking, default_package)
            else:
                packages = self.carrier._get_packages_from_order(order, default_package)

            dimensions = {}
            if len(packages) == 1:
                dimensions = {
                    'length': packages[0].dimension['length'],
                    'width': packages[0].dimension['width'],
                    'height': packages[0].dimension['height']
                }
            total_weight = sum(pack.weight for pack in packages)
        weight_in_g = self.carrier._correios_convert_weight(total_weight)
        rate_dict = self._get_rate(shipper, recipient, weight_in_g, dimensions)
        return rate_dict

    def _check_required_value(self, recipient, shipper, products):
        """
        Check if the required value are not present in order to process an API call.
        return True or return an error if configuration is missing.
        """
        customer=_('Customer')
        shipper_=_('Shipper')
        error_msg = {customer: [], shipper_: []}
        if not recipient.vat:
            error_msg[customer].append(_("CNPJ/CPF is required!"))
        if not recipient.street:
            error_msg[customer].append(_("Street is required!"))
        if not recipient.zip:
            error_msg[customer].append(_("Pincode is required!"))
        if not recipient.country_id:
            error_msg[customer].append(_("Country is required!"))
        if not recipient.email:
            error_msg[customer].append(_("Email is required!"))
        if not recipient.phone and not recipient.mobile:
            error_msg[customer].append(_("Phone or Mobile is required!"))
        if not shipper.vat:
            error_msg[shipper_].append(_("CNPJ/CPF is required!"))
        if not shipper.street:
            error_msg[shipper_].append(_("Street is required!"))
        if not shipper.zip:
            error_msg[shipper_].append(_("Pincode is required!"))
        if not shipper.country_id:
            error_msg[shipper_].append(_("Country is required!"))
        if not shipper.email:
            error_msg[shipper_].append(_("Email is required!"))
        if not shipper.phone and not shipper.mobile:
            error_msg[shipper_].append(_("Phone or Mobile is required!"))
        for product in products:
            if not product.weight:
                error_msg.setdefault(product.name, [])
                error_msg[product.name].append(_("Weight is missing!"))
            if not product.default_code:
                error_msg.setdefault(product.name, [])
                error_msg[product.name].append(_("SKU is missing!"))
        if error_msg:
            msg = "".join(e_for + "\n- " + "\n- ".join(e) + "\n" for e_for, e in error_msg.items() if e)
            if msg:
                raise ValidationError(msg)
        return True

    def _correios_get_error_message(self, json_data):
        """
        Return error message(s) from correios requests.
        """
        errors = json_data.get('errors', {})
        payload = json_data.get('payload', {})
        message = ''

        if errors:
            for value in errors.values():
                sub_msg = "\n".join(value) if isinstance(value, list) else value or ''
                message += _("Correios Error: %s", sub_msg) + '\n'
        elif 'message' in json_data:
            message = _("Correios Error: %s", json_data['message'])
        elif 'error_message' in payload:
            message = _("Correios Error: %s", payload['error_message'])
        elif 'awb_assign_error' in payload:
            message = _('Correios Error: %s', payload['awb_assign_error'])
        elif not json_data.get('label_created') and 'response' in json_data:
            message = _('Correios Error: %s', json_data['response'])
        return message


    def _get_delivery_services(self, origin_partner):
        self._validate_partner_fields(origin_partner)
        return self._send_request('deliveryservices', method='POST', data={
            'street': origin_partner.street,
            'post_code': origin_partner.zip,
            'country_code': origin_partner.country_code,
            'packages': [{}],
        })

    @staticmethod
    def _validate_partner_fields(partner):
        """ Make sure that the essential fields are filled in. Other error specific to each carrier could still arise,
        but this should prevent too many errors with starshipit.
        """
        required_address_fields = ['street', 'city', 'country_id', 'state_id']
        fields_details = partner.fields_get(required_address_fields, ['string'])
        for field in required_address_fields:
            if not partner[field]:
                field_name = fields_details[field]['string']
                raise UserError(_('Please fill in the fields %s on the %s partner.', field_name, partner.name))
    def _get_shipping_lines(self, package, picking):
        """
        Returns shipping products data from picking to create an order.
        Get shipping lines from package commodities.
        """
        line_by_product = {}
        package_total_value = 0
        for commodity in package.commodities:
            moves = picking.env['stock.move']
            for move in picking.move_ids:
                if move.product_id != commodity.product_id:
                    continue
                if package.name == "Bulk Content":
                    if any(not ml.result_package_id for ml in move.move_line_ids):
                        moves |= move
                else:
                    if any(ml.result_package_id.name == package.name for ml in move.move_line_ids):
                        moves |= move
            dest_moves = picking.env['stock.move']
            for move in picking.move_ids:
                move_dest = move._rollup_move_dests(set())
                if move_dest:
                    # need only those moves which have sale_line_id links for 3 step delivery
                    dest_moves |= picking.env['stock.move'].browse(move_dest)
            if dest_moves:
                moves = dest_moves
            # label price must be in the INR currency
            unit_price = self._get_currency_converted_amount(round(commodity.monetary_value, 2), package.picking_id)
            line_by_product[commodity.product_id.id] = {
                "name": commodity.product_id.name,
                "sku": commodity.product_id.default_code or "",
                "units": commodity.qty,
                "selling_price": unit_price,
                "hsn": commodity.product_id.hs_code or "",
                # "tax": self._get_gst_tax_rate(moves),
            }
            package_total_value += unit_price * commodity.qty
        if package_total_value > 50000 and not picking.eway_bill_number:
            raise ValidationError(_(
                'Eway Bill number is required to ship an order if order amount is more than 50,000 INR.'
            ))
        return line_by_product

    def _get_currency_converted_amount(self, amount, picking):
        """
        Returns the amount converted in INR currency.
        """
        inr_currency = picking.env.ref('base.INR')
        picking_currency = picking.sale_id.currency_id if picking.sale_id else picking.company_id.currency_id
        if picking_currency.id != inr_currency.id:
            return picking_currency._convert(amount, inr_currency, picking.company_id or picking.env.company,
                                             picking.date_done or datetime.today())
        return amount



