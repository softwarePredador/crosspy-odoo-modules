import re
import logging
from odoo import models

_logger = logging.getLogger(__name__)

try:
    import brazilcep
except ImportError:
    _logger.error("Cannot import zeep library", exc_info=True)


class ZipSearchMixin(models.AbstractModel):
    _name = 'zip.search.mixin'
    _description = 'Pesquisa de CEP'

    def search_address_by_zip(self, zip_code):
        """
            {
                'district': 'rua abc',
                'cep': '37503130',
                'city': 'city ABC',
                'street': 'str',
                'uf': 'str',
                'complement': 'str',
            }
        """
        zip_code = re.sub('[^0-9]', '', zip_code or '')
        
        try:
            res = brazilcep.get_address_from_cep(zip_code)
        except:
            return {}
        state = self.env['res.country.state'].search(
            [('country_id.code', '=', 'BR'),
             ('code', '=', res['uf'])])

        city = self.env['res.city'].search([
            ('name', '=ilike', res['city']),
            ('state_id', '=', state.id)])
        
        if(res['street'] == None):
            res['street'] = False
        if(res['complement'] == None):
            res['complement'] = False
        if(res['district'] == None):
            res['district'] = False
        
        return {
            'zip': zip_code,
            'street': res['street'],
            'street2': res['complement'],
            'district': res['district'],
            'country_id': state.country_id.id,
            'state_id': state.id,
            'city_id': city.id
        }
