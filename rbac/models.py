from django.db import models


class ErpPermission(models.Model):
    """No table. This unmanaged model only exists so Django creates the ERP
    action permissions (approve / reject / cancel / post / reverse / print /
    export / import / pay) next to the usual view/add/change/delete ones."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("approve_document", "Can approve documents"),
            ("reject_document", "Can reject documents"),
            ("cancel_document", "Can cancel documents"),
            ("post_document", "Can post documents (accounting / stock)"),
            ("reverse_document", "Can reverse posted documents"),
            ("print_document", "Can print documents"),
            ("export_document", "Can export data"),
            ("import_document", "Can import data"),
            ("pay_document", "Can make / receive payments"),
        ]
