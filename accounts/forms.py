from django import forms
from django.contrib.auth.models import Group

from .models import User


class RoleForm(forms.ModelForm):
    """A 'Role' shown in the app is just a Django auth Group under a
    friendlier name — permissions are rendered as a checkbox grid grouped
    by app directly in accounts/role_form.html (built from the Permission
    queryset the view passes in), not through this form, so the form only
    needs to own the name."""

    class Meta:
        model = Group
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. Cashier, Store Manager"})}


class StaffUserForm(forms.ModelForm):
    """Create / edit a staff member.

    Pass `editor=request.user`. Rules enforced here (not just hidden in the template):
      * only a Super Admin can make someone else a Super Admin;
      * a non-Super-Admin can only hand out roles whose permissions they hold themselves
        (so nobody can escalate their own or another user's access);
      * the last active Super Admin can't be demoted or deactivated;
      * a new user must get a password.
    """
    password = forms.CharField(
        label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), required=False,
        help_text="Required for a new user. When editing, leave blank to keep the current password.",
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple, label="Roles",
    )
    is_super_admin = forms.BooleanField(
        required=False, label="Super Admin",
        help_text="Full access to every screen and action in the system. Give this to the owner / IT lead only.",
    )

    class Meta:
        model = User
        fields = [
            "username", "first_name", "last_name", "email", "phone",
            "store", "is_biller", "is_staff", "is_active", "groups",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"autocomplete": "off"}),
        }
        help_texts = {"is_staff": "Can open the back-office screens. Roles still decide what they can do there."}

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        from rbac.backends import SUPER_ADMIN_ROLE, is_super_admin
        self.editor = editor
        self.editor_is_super = editor is None or is_super_admin(editor)
        self._super_role = SUPER_ADMIN_ROLE

        roles = Group.objects.exclude(name=SUPER_ADMIN_ROLE).order_by("name")
        self._locked_role_ids = set()
        if not self.editor_is_super:
            held = set(editor.get_all_permissions())
            allowed = []
            for role in roles.prefetch_related("permissions__content_type"):
                role_perms = {f"{p.content_type.app_label}.{p.codename}" for p in role.permissions.all()}
                if role_perms <= held:
                    allowed.append(role.pk)
            if self.instance.pk:
                self._locked_role_ids = set(self.instance.groups.exclude(pk__in=allowed).values_list("pk", flat=True))
            roles = roles.filter(pk__in=allowed)
            self.fields.pop("is_super_admin")
        self.fields["groups"].queryset = roles
        if editor is not None and "store" in self.fields:               # a company admin can only place staff in their own branches
            from billing.access import company_of, is_platform_owner
            from billing.tenancy import multi_company
            company = None if is_platform_owner(editor) or not multi_company() else company_of(editor)
            if company is not None:
                self.fields["store"].queryset = self.fields["store"].queryset.filter(company=company)

        if self.instance.pk:
            self.fields["password"].help_text = "Leave blank to keep the current password."
            if self.editor_is_super:
                self.fields["is_super_admin"].initial = is_super_admin(self.instance)
        elif "is_super_admin" in self.fields:
            self.fields["is_super_admin"].initial = False

    def _is_last_super_admin(self):
        from rbac.backends import is_super_admin
        if not self.instance.pk or not is_super_admin(self.instance):
            return False
        others = User.objects.filter(is_active=True).exclude(pk=self.instance.pk)
        return not (others.filter(is_superuser=True) | others.filter(groups__name=self._super_role)).exists()

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if password:
            from django.contrib.auth.password_validation import validate_password
            validate_password(password, self.instance if self.instance.pk else None)
        return password

    def clean(self):
        data = super().clean()
        if not self.instance.pk and not data.get("password"):
            self.add_error("password", "Set a password for the new user.")
        if self._is_last_super_admin():
            if not data.get("is_active", True):
                self.add_error("is_active", "This is the last active Super Admin - it can't be deactivated.")
            if "is_super_admin" in self.fields and not data.get("is_super_admin"):
                self.add_error("is_super_admin", "This is the last active Super Admin - make someone else Super Admin first.")
        if self.editor is not None and self.instance.pk == getattr(self.editor, "pk", None) and not data.get("is_active", True):
            self.add_error("is_active", "You can't deactivate your own account.")
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        make_super = self.cleaned_data.get("is_super_admin") if "is_super_admin" in self.fields else None
        if make_super:
            user.is_superuser = True
            user.is_staff = True
        if commit:
            user.save()
            self._save_roles(user, make_super)
        else:
            self.save_m2m = lambda: self._save_roles(user, make_super)
        return user

    def _save_roles(self, user, make_super):
        """Selected roles + roles the editor may not touch + (unless changed below) the Super Admin role."""
        selected = set(self.cleaned_data["groups"].values_list("pk", flat=True)) if self.cleaned_data.get("groups") is not None else set()
        keep_super = set(user.groups.filter(name=self._super_role).values_list("pk", flat=True)) if make_super is None else set()
        user.groups.set(selected | self._locked_role_ids | keep_super)
        if make_super is not None:
            role, _ = Group.objects.get_or_create(name=self._super_role)
            if make_super:
                user.groups.add(role)
            else:
                user.groups.remove(role)       # rbac.signals clears is_superuser


class ProfileForm(forms.ModelForm):
    """What a signed-in person may change about themselves (never roles, store or flags)."""
    remove_photo = forms.BooleanField(required=False, label="Remove my photo")

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone", "photo"]
        labels = {"first_name": "First name", "last_name": "Last name", "email": "Email", "phone": "Phone", "photo": "Photo"}
        widgets = {"photo": forms.FileInput(attrs={"accept": "image/*"}), "phone": forms.TextInput(attrs={"type": "tel"}),
                   "email": forms.EmailInput(attrs={"autocomplete": "email"})}

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and hasattr(photo, "size") and photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("The photo must be smaller than 2 MB.")
        return photo

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("remove_photo") and not self.files.get("photo") and user.photo:
            user.photo.delete(save=False)
            user.photo = None
        if commit:
            user.save()
        return user
