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
    password = forms.CharField(
        label="Password", widget=forms.PasswordInput, required=False,
        help_text="Leave blank to keep the current password (only used when creating a new user or resetting one).",
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all().order_by("name"), required=False,
        widget=forms.CheckboxSelectMultiple, label="Roles",
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

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
            self.save_m2m()
        return user


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
