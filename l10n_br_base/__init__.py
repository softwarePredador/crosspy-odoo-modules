from .hooks import pre_init_hook

from . import models

from odoo.addons import account
from odoo import api, tools, SUPERUSER_ID

# Install Simple Chart of Account Template for Brazilian Companies
_auto_install_l10n_original = account._auto_install_l10n


def _auto_install_l10n_br_generic_module(env):
    country_code = env.company.country_id.code
    if country_code and country_code.upper() == "BR":
        if (
            hasattr(env.user.company_id, "tax_framework")
            and env.company.tax_framework == "3"
        ):
            module_name_domain = [("name", "=", "l10n_br_coa_generic")]
        else:
            module_name_domain = [("name", "=", "l10n_br_coa_simple")]

        # Load all l10n_br COA in Demo
        if not tools.config["without_demo"]:
            module_name_domain = [
                ("name", "in", ("l10n_br_coa_simple", "l10n_br_coa_generic"))
            ]

        module_ids = env["ir.module.module"].search(
            module_name_domain + [("state", "=", "uninstalled")]
        )
        module_ids.sudo().button_install()
    else:
        _auto_install_l10n_original(env)


account._auto_install_l10n = _auto_install_l10n_br_generic_module
