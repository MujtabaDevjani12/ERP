"""The "enter the code we e-mailed you" page, shared by sign-up and forgot-password."""
from django.contrib import messages
from django.shortcuts import redirect, render

from . import otp


def verify_page(request, purpose, *, title, lead, back_url, back_label, on_success):
    """GET shows the form; POST checks the code (or sends a new one). on_success(request, state) returns the response to give."""
    state = otp.peek(request, purpose)
    if state is None:
        messages.error(request, "Your code has expired or nothing was started here. Please start again.")
        return redirect(back_url)
    error = notice = ""
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            try:
                otp.resend(request, purpose)
                notice = "A new code is on its way."
            except otp.OtpError as exc:
                error = str(exc)
        else:
            try:
                state = otp.verify(request, purpose, request.POST.get("code", ""))
            except otp.OtpError as exc:
                error = str(exc)
            else:
                return on_success(request, state)
        state = otp.peek(request, purpose)
        if state is None:                                           # too many wrong tries / expired meanwhile
            messages.error(request, error or "Your code has expired. Please start again.")
            return redirect(back_url)
    return render(request, "registration/otp_verify.html", {
        "title": title, "lead": lead, "masked": otp.mask(state["email"]), "error": error, "notice": notice,
        "wait": otp.seconds_to_resend(state), "back_url": back_url, "back_label": back_label})
