"""
ERP-side business rules: creating a company from the sign-up page. Everything about plans, invoices and payments is in
ownerconsole.services (the owner database); this module only touches ERP data and then hands the company over to the owner side.
"""
import logging
import re

from django.contrib.auth.models import Group
from django.db import transaction

from core.errors import ERPError
from ownerconsole import services as owner_services
from ownerconsole.models import Subscription
from .models import GoogleIdentity

logger = logging.getLogger(__name__)
COMPANY_ADMIN_ROLE = "Company Admin"

default_plan = owner_services.default_plan
trial_days_for = owner_services.trial_days_for
refresh_company = owner_services.refresh_company


def ensure_company_admin_role():
    group = Group.objects.filter(name=COMPANY_ADMIN_ROLE).first()
    if group is None or not group.permissions.exists():
        from django.core.management import call_command
        call_command("seed_roles", verbosity=0)
        group = Group.objects.get(name=COMPANY_ADMIN_ROLE)
    return group


def unique_username(base):
    from accounts.models import User
    base = re.sub(r"[^\w.@+-]", "", base)[:130] or "user"
    name, n = base, 1
    while User.objects.filter(username__iexact=name).exists():
        n += 1
        name = f"{base}{n}"
    return name


@transaction.atomic
def create_company_with_owner(*, company_name, owner_name, email, password=None, phone="", plan=None, interval=Subscription.MONTHLY,
                              google_sub="", business_type="general", password_hash=""):
    """Sign-up: a new Company + first branch + warehouse + chart of accounts + the admin login, then the subscription.

    The subscription starts "pending" (or with demo days if the owner offers them): the ERP stays locked until the first invoice is paid.
    Returns (company, user, subscription).
    """
    from accounts.models import User
    from accounting.management.commands.seed_chart_of_accounts import seed_chart
    from company.models import Company
    from payments.models import PaymentMethod
    from tenants.models import Store, Warehouse

    email = (email or "").strip().lower()
    if not company_name.strip():
        raise ERPError("Please enter your company name.")
    if User.objects.filter(email__iexact=email).exists():
        raise ERPError("An account with this email already exists. Sign in instead.")
    plan = plan or default_plan()
    if plan is None:
        raise ERPError("Sign-up is not open yet: no plan has been published. Please contact us.")

    company = Company.objects.create(name=company_name.strip(), email=email, phone=phone)
    store = Store.objects.create(name=company_name.strip(), company=company, email=email, phone=phone, business_type=business_type)
    Warehouse.objects.create(store=store, name="Main warehouse", is_default=True)
    PaymentMethod.ensure_defaults(store)
    seed_chart(company)

    first, _, last = (owner_name or "").strip().partition(" ")
    user = User(username=unique_username(email), email=email, first_name=first[:150], last_name=last[:150], phone=phone,
                store=store, is_biller=True, is_staff=False)
    if password_hash:                                              # already hashed (sign-up keeps only the hash while the e-mail is verified)
        user.password = password_hash
    elif password:
        user.set_password(password)
    else:
        user.set_unusable_password()
    user.save()
    user.groups.add(ensure_company_admin_role())
    if google_sub:
        GoogleIdentity.objects.create(user=user, sub=google_sub, email=email)

    try:
        sub = owner_services.register_company(company_id=company.pk, company_name=company.name, company_email=email, company_phone=phone,
                                              plan=plan, interval=interval, by=user)
    except ERPError:
        raise
    except Exception as exc:                                       # noqa: BLE001 - the owner database could not be written: undo the ERP rows too
        logger.exception("Owner database write failed during sign-up")
        raise ERPError("We could not finish creating your account right now. Please try again in a few minutes.") from exc
    return company, user, sub
