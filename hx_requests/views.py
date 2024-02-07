import importlib
import inspect
from typing import Any, Dict

from django.apps import apps
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie

from hx_requests.utils import deserialize_kwargs, is_htmx_request


@method_decorator(ensure_csrf_cookie, name="dispatch")
class HtmxViewMixin:
    """
    Mixin to be added to views that are using HXRequests.
    Hijacks the get and post to route them to the proper
    HXRequest.
    """

    hx_requests: Dict = []

    def dispatch(self, request, *args, **kwargs):
        # Try to dispatch to the right method; if a method doesn't exist,
        # defer to the error handler. Also defer to the error handler if the
        # request method isn't on the approved list.
        if request.method.lower() in self.http_method_names:
            handler_class = self
            # If it's an HTMX request, use the HXRequest class to handle the request
            # otherwise, use the view class to handle the request.
            if is_htmx_request(request):
                handler_class = self._setup_hx_request(request, *args, **kwargs)
                kwargs = self.get_hx_extra_kwargs(request)
            handler = getattr(
                handler_class, request.method.lower(), self.http_method_not_allowed
            )
        else:
            handler = self.http_method_not_allowed
        return handler(request, *args, **kwargs)

    def get_hx_request(self, request):
        hx_request_name = request.GET.get("hx_request_name")
        self._get_hx_request_classes()
        try:
            hx_request = next(
                hx_request
                for name, hx_request in self.hx_requests.items()
                if name == hx_request_name
            )
        except StopIteration:
            raise Exception(
                f"No HXRequest found with the name {hx_request_name}. Are you sure it's spelled correctly?"
            )
        return hx_request()

    @classmethod
    def _get_hx_request_classes(cls):
        from .hx_requests import BaseHXRequest

        # If the hx_requests are already set don't need to do the whole collection.
        if getattr(cls, "hx_requests", None):
            return
        modules = []
        hx_request_classes = {}
        for app in apps.get_app_configs():
            try:
                modules.append(importlib.import_module(f"{app.label}.hx_requests"))
            except ModuleNotFoundError:
                pass
        for module in modules:
            clsmembers = inspect.getmembers(module, inspect.isclass)
            for _, obj in clsmembers:
                if issubclass(obj, BaseHXRequest) and getattr(obj, "name", None):
                    hx_request_classes[obj.name] = obj
        cls.hx_requests = hx_request_classes

    def get_hx_extra_kwargs(self, request):
        kwargs = {}
        for key in request.GET:
            kwargs[key] = request.GET.get(key)

        return deserialize_kwargs(**kwargs)

    def _setup_hx_request(self, request, *args, **kwargs):
        # Call get even on post to setup neccessary parts for the context
        super().get(request, *args, **kwargs)
        hx_request = self.get_hx_request(request)
        hx_request.view = self
        hx_request.setup_hx_request(request)
        return hx_request
