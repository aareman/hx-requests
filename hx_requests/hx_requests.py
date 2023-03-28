import json
from typing import Dict
from django.utils.html import strip_tags

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.forms import Form
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe


class BaseHXRequest:
    """
    HXRequest is the base class for Htmx requests.
    """

    name: str = ""
    hx_object_name: str = "hx_object"

    def get_context_data(self, **kwargs) -> Dict:
        context = self.view.get_context_data()
        context["hx_kwargs"] = kwargs
        context[self.hx_object_name] = self.hx_object
        context["request"] = self.request
        context["hx_request"] = self
        return context

    def get_hx_object(self, request):
        if request.GET.get("object"):
            serialized_hx_object = request.GET.get("object")
            app_label, model, pk = serialized_hx_object.split("_")
            model = apps.get_model(app_label, model)
            return model.objects.get(pk=pk)

    def setup_hx_request(self, request):
        self.request = request

        # TODO maybe remove this line (why is it there?)
        if not hasattr(self, "hx_object"):
            self.hx_object = self.get_hx_object(request)

    def hx_object_to_str(self) -> str:
        return (
            self.hx_object._meta.model.__name__.capitalize() if self.hx_object else ""
        )


class HXRequestGET(BaseHXRequest):
    GET_template: str = ""

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        return self.get_GET_response(**kwargs)

    def get_GET_context_data(self, **kwargs) -> dict:
        context = self.get_context_data(**kwargs)
        return context

    def get_GET_headers(self, **kwargs) -> Dict:
        return {}

    def get_GET_response(self, **kwargs):
        html = render_to_string(
            self.GET_template, self.get_GET_context_data(**kwargs), self.request
        )
        return HttpResponse(
            html,
            headers=self.get_GET_headers(**kwargs),
        )


class HXRequestPOST(BaseHXRequest):
    POST_template: str = ""
    refresh_page: bool = False
    redirect: str = None
    show_messages: bool = getattr(settings, "HX_REQUESTS_SHOW_MESSAGES", False)
    message: str = ""
    level = "success"

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.message = self.get_success_message(**kwargs)
        self.level = "success"
        return self.get_POST_response(**kwargs)

    def get_POST_context_data(self, **kwargs) -> dict:
        context = self.get_context_data(**kwargs)
        return context

    def get_POST_headers(self, **kwargs) -> Dict:
        headers = {}
        if self.refresh_page:
            headers["HX-Refresh"] = "true"
        elif self.redirect:
            headers["HX-Redirect"] = self.redirect
        if self.show_messages:
            headers.update(self.get_message_headers(**kwargs))
        return headers

    def get_POST_response(self, **kwargs):
        if self.refresh_page or self.redirect:
            if self.show_messages:
                self.set_synchronous_messages(**kwargs)
            return HttpResponse(headers=self.get_POST_headers(**kwargs))

        if self.hx_object and self.hx_object.pk:
            self.hx_object.refresh_from_db()

        html = render_to_string(
            self.POST_template, self.get_POST_context_data(**kwargs), self.request
        )
        return HttpResponse(
            html,
            headers=self.get_POST_headers(**kwargs),
        )

    def get_success_message(self, **kwargs) -> str:
        return self.message

    def get_message_headers(self, **kwargs) -> Dict:
        headers = {}

        headers["HX-Trigger"] = json.dumps(
            {"showMessages": {"message": self.message, "level": self.level}}
        )
        return headers

    def set_synchronous_messages(self, **kwargs):
        messages.success(self.request, self.message)


class FormHXRequest(HXRequestGET, HXRequestPOST):
    """
    Adds in form to context.
    On POST if the form is valid returns form_valid OR the POST_template.
    If the form is invalid returns form_invalid or the GET_template.

    form_invalid and form_valid can be overriden for custom behavior.
    Can override get_template_kwargs to add custom kwargs into the form.
    """

    form_class: Form = None
    add_form_errors_to_error_message: bool = False

    def get_context_data(self, **kwargs) -> Dict:
        context = super().get_context_data(**kwargs)
        context["form"] = self.form
        return context

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.form = self.form_class(**self.get_form_kwargs(**kwargs))
        return self.get_GET_response(**kwargs)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.form = self.form_class(**self.get_form_kwargs(**kwargs))

        if self.form.is_valid():
            self.message = self.get_success_message(**kwargs)
            self.level = "success"
            return self.form_valid(**kwargs)

        else:
            self.message = self.get_error_message(**kwargs)
            self.level = "danger"
            return self.form_invalid(**kwargs)

    def form_valid(self, **kwargs) -> str:
        self.form.save()
        return self.get_POST_response(**kwargs)

    def form_invalid(self, **kwargs) -> str:
        return self.get_GET_response(**kwargs)

    def get_GET_headers(self, **kwargs) -> Dict:
        """
        For when the form is invalid
        """
        headers = super().get_GET_headers()
        if self.request.method == "POST":
            headers.update(self.get_message_headers(**kwargs))
        return headers

    def get_form_kwargs(self, **kwargs):
        """Return the keyword arguments for instantiating the form."""
        form_kwargs = {"initial": self.get_initial(**kwargs)}
        if self.request.method in ("POST", "PUT"):
            form_kwargs.update(
                {
                    "data": self.request.POST,
                    "files": self.request.FILES,
                }
            )
        if getattr(self, "hx_object"):
            form_kwargs.update({"instance": self.hx_object})
        return form_kwargs

    def get_initial(self, **kwargs):
        return {}

    def get_success_message(self, **kwargs) -> str:
        message = self.message or (
            f"{self.hx_object_to_str()} Saved Successfully."
            if self.hx_object
            else "Saved Successfully"
        )
        return message

    def get_error_message(self, **kwargs) -> str:
        message = self.message or (
            f"{self.hx_object_to_str()} did not save  successfully."
            if self.hx_object
            else "Did not save successfully"
        )
        if self.add_form_errors_to_error_message:
            message += mark_safe("</br>") + self.get_form_errors(**kwargs)
        return message

    def get_form_errors(self, **kwargs) -> str:
        errors = ""
        for k, v in self.form.errors.items():
            errors += f"{k}: {strip_tags(v)}" if k != "__all__" else strip_tags(v)
        return errors


class DeleteHXRequest(HXRequestPOST):
    """
    HXRequest for deleting objects. Can override handle_delete
    for custom behavior.
    """

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.message = self.get_success_message(**kwargs)
        self.level = "success"
        return self.handle_delete(**kwargs)

    def handle_delete(self, **kwargs) -> str:
        self.hx_object.delete()
        return self.get_POST_response(**kwargs)

    def get_success_message(self, **kwargs) -> str:
        message = self.message or (
            f"{self.hx_object_to_str()} deleted successfully."
            if self.hx_object
            else "Deleted successfully"
        )
        return message
