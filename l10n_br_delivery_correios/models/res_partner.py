# -*- coding: utf-8 -*-
from odoo import fields, models
from .correios_request import CorreiosProvider


class ResPartner(models.Model):
    _inherit = 'res.partner'

    property_delivery_modalidade_frete = fields.Selection(
        [('0', '0 - Contratação do Frete por conta do Remetente (CIF)'),
         ('1', '1 - Contratação do Frete por conta do Destinatário (FOB)'),
         ('2', '2 - Contratação do Frete por conta de Terceiros'),
         ('3', '3 - Transporte Próprio por conta do Remetente'),
         ('4', '4 - Transporte Próprio por conta do Destinatário'),
         ('9', '9 - Sem Ocorrência de Transporte')], 
         company_dependent=True, string="Fleet mode", help="Default fleet mode method used in sales orders.")

    def search_address_by_zip(self, zip_code):
        carrier = self.env['delivery.carrier'].search([('delivery_type', '=', 'correios'), ('correios_password', '!=', False)], limit=1)

        sr = CorreiosProvider(carrier)
        res = sr._get_address_cep(partner_zip=zip_code)

        state = self.env['res.country.state'].search(
            [('country_id.code', '=', 'BR'),
             ('code', '=', res['uf'])])

        city = self.env['res.city'].search([
            ('name', '=ilike', res['localidade']),
            ('state_id', '=', state.id)])
        
        if(res.get('abreviatura', None) == None):
            res['abreviatura'] = False
        if(res.get('complemento', None) == None):
            res['complemento'] = False
        if(res.get('nomeMunicipio', None) == None):
            res['nomeMunicipio'] = False
        
        return {
            'zip': res['cep'],
            'street': res['abreviatura'],
            'street2': res['complemento'],
            'district': res['nomeMunicipio'],
            'country_id': state.country_id.id,
            'state_id': state.id,
            'city_id': city.id
        }

        
