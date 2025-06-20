import logging
import base64
from os import path
from zipfile import ZipFile
from io import BytesIO
from lxml import objectify, etree

from odoo import api, models, fields
from odoo.exceptions import UserError, RedirectWarning

_logger = logging.getLogger(__file__)


class WizardImportNfe(models.TransientModel):
    _name = 'wizard.import.nfe'
    _description = "Wizard Importacao NFE"

    state = fields.Selection([('ok', 'OK'), ('error', 'Erro')], default='ok')
    import_batch_zip = fields.Boolean(
        string="Importar Zip?", help="Se marcado esta opção é possível \
        importar um arquivo compactado com vários arquivos. Apenas arquivos \
        com a extensão .xml serão importados")
    nfe_xml = fields.Binary('XML da NFe')
    zip_file = fields.Binary('Arquivo ZIP')
    zip_file_error = fields.Binary('Arquivos não importados!', readonly=True)
    zip_file_error_name = fields.Char(
        default="XMLs não importados.zip", readonly=True)
    skip_wrong_xml = fields.Boolean(
        string="Ignorar Xml com erro?", help="Se marcado vai ignorar os xmls \
        que estão com erro e importar o restante! Os xmls com erro serão \
        disponibilizados para download", default=True)

    def _unzip_xml_files(self):
        zip_memory = base64.b64decode(self.zip_file)
        xml_list = []

        with ZipFile(BytesIO(zip_memory)) as thezip:
            for zipinfo in thezip.infolist():
                if not zipinfo.filename.lower().endswith('.xml'):
                    continue
                with thezip.open(zipinfo) as thefile:
                    xml_list.append(
                        {'name': path.basename(zipinfo.filename),
                         'file': thefile.read()})
        return xml_list

    def _zip_xml_files(self, xml_list):
        mem_file = BytesIO()
        with ZipFile(mem_file, mode='w') as thezip:
            for xml in xml_list:
                with thezip.open(xml['name'], mode='w') as thefile:
                    thefile.write(xml['file'])
        self.zip_file_error = base64.b64encode(mem_file.getvalue())

    def _import_xml(self, xml):
        # Clean xml
        xml = xml.replace(b'xmlns="http://www.prefeitura.sp.gov.br/nfe"', b'')
        try:
            nfe = objectify.fromstring(xml)
        except etree.XMLSyntaxError as e:
                # Handle bad XML input
                _logger.error("Failed to parse XML: %s", e)
                return None
        # Check if is a NFSe Paulistana
        # ns = {'nfe': 'http://www.prefeitura.sp.gov.br/nfe'}

        # # If you're using XPath (e.g., to find the root NFe):
        # nfe_node = nfe.xpath('//nfe:NFe', namespaces=ns)
        # if nfe_node:
        #     nfe = nfe_node[0]

        invoice_eletronic = self.env['eletronic.document']
        company_id = self.env.company
        edi_doc = self.env['eletronic.document']
        if edi_doc.existing_invoice(nfe):
            raise UserError('Documento Eletrônico já importado!')
        if hasattr(nfe, 'NFe') and hasattr(nfe.NFe, 'ChaveNFe') and hasattr(nfe.NFe, 'ChaveRPS'):
            invoice_eletronic.import_nfse(
                company_id, nfe.NFe, xml, company_id.partner_automation,
                company_id.invoice_automation, company_id.tax_automation,
                company_id.supplierinfo_automation, False, False)
        else:            
            invoice_eletronic.import_nfe(
                company_id, nfe, xml, company_id.partner_automation,
                company_id.invoice_automation, company_id.tax_automation,
                company_id.supplierinfo_automation, False, False)
            

    def action_import_nfe(self):
        
        if not self.nfe_xml and not self.zip_file:
            raise UserError('Por favor, insira um arquivo de NFe.')

        xml_list = []
        if self.import_batch_zip:
            xml_list = self._unzip_xml_files()
            if len(xml_list) == 0:
                raise UserError('Nenhum xml encontrado no arquivo')
        else:
            xml_list.append(
                {'name': 'NF-e', 'file': base64.b64decode(self.nfe_xml)})

        error_xml = []
        for xml in xml_list:
            try:
                self._import_xml(xml['file'])
            except (UserError, RedirectWarning) as e:
                msg = "Erro ao importar o xml: {0}\n{1}".format(
                    xml['name'], str(e))
                _logger.warning(msg)
                if self.skip_wrong_xml and self.import_batch_zip:
                    error_xml.append(xml)
                else:
                    raise UserError(msg)
        if len(error_xml) > 0:
            self.state = 'error'
            self._zip_xml_files(error_xml)
            return {
                "type": "ir.actions.act_window",
                "res_model": "wizard.import.nfe",
                "views": [[False, "form"]],
                "name": "Importação de XML da NF-e",
                "target": "new",
                "res_id": self.id,
            }
