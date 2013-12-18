# coding: utf-8
from __future__ import absolute_import
import inspect

from celery import Task
from kombu.utils import symbol_by_name
from django.db.models.base import Model
from rest_framework.serializers import ModelSerializer

from . import config
from .app import rpc
from .exceptions import RestFrameworkError


class ModelTask(Task):
    """ Base task for operating with django models.
    """
    abstract = True

    def __call__(self, model,  *args, **kwargs):
        """ Prepare context for calling task function.
        """
        self.request.model = self._import_model(model)
        args = [model] + list(args)
        return self.run(*args, **kwargs)

    @staticmethod
    def _import_model(model_name):
        """ Import class by full name, check type and return.
        """
        sym = symbol_by_name(model_name)
        if inspect.isclass(sym) and issubclass(sym, Model):
            return sym
        raise TypeError(
            "Symbol '{}' is not a Django model".format(model_name))

    @staticmethod
    def _create_queryset(model):
        """ Construct queryset by params.
        """
        return model.objects.all()

    def _create_serializer_class(self, model_class):
        """ Return REST framework serializer class for model.
        """
        class GenericModelSerializer(ModelSerializer):
            class Meta:
                model = model_class

            def get_identity(self, data):
                pk_name = self.Meta.model._meta.pk.attname
                try:
                    return data.get('pk', data.get(pk_name, None))
                except AttributeError:
                    return None

        return GenericModelSerializer

    @property
    def model(self):
        return self.request.model

    @property
    def pk_name(self):
        return self.model._meta.pk.attname

    @property
    def default_queryset(self):
        return self._create_queryset(self.model)

    @property
    def serializer_class(self):
        return self._create_serializer_class(self.model)


@rpc.task(name='celery_rpc.filter', bind=True, base=ModelTask)
def filter(self, model, filters=None, offset=0,
           limit=config.FILTER_LIMIT, fields=None,  exclude=[],
           depth=0, manager='objects', database=None, *args, **kwargs):
    """ Filter Django models and return serialized queryset.

    :param model: full name of model class like 'app.models:Model'
    :param filters: filter supported by model manager like {'pk__in': [1,2,3]}
    :param offset: offset of first item in the queryset (by default 0)
    :param limit: max number of result list (by default 1000)
    :return: list of serialized model data

    """
    filters = filters if isinstance(filters, dict) else {}
    qs = self.default_queryset.filter(**filters)[offset:offset+limit]
    return self.serializer_class(instance=qs, many=True).data


@rpc.task(name='celery_rpc.update', bind=True, base=ModelTask)
def update(self, model_name, data, fields=None, nocache=False,
           manager='objects', database=None, *args, **kwargs):
    """ Update Django models by PK and return new values.

    :param model_name: model class like 'app.models:ModelClass'
    :param data: values of one or several objects
        {'id': 1, 'title': 'hello'} or [{'id': 1, 'title': 'hello'}]
    :return: serialized model data or list of one or errors

    """
    pk_name = self.pk_name
    get_pk_value = lambda item: item.get('pk', item.get(pk_name))
    if isinstance(data, dict):
        instance = self.default_queryset.get(pk=get_pk_value(data))
        many = False
    else:
        pk_values = [get_pk_value(item) for item in data]
        instance = self.default_queryset.filter(pk__in=pk_values)
        many = True

    serializer = self.serializer_class(instance=instance, data=data, many=many)
    if not serializer.errors:
        serializer.save()
        return serializer.data
    else:
        raise RestFrameworkError('Serializer errors happened',
                                 serializer.errors)


class FunctionTask(Task):
    """ Base task for calling function.
    """
    abstract = True

    def __call__(self, function,  *args, **kwargs):
        """ Prepare context for calling task function.
        """
        self.request.function = self._import_model(function)
        args = [function] + list(args)
        return self.run(*args, **kwargs)

    @staticmethod
    def _import_function(func_name):
        """ Import class by full name, check type and return.
        """
        sym = symbol_by_name(func_name)
        if inspect.isfunction(sym):
            return sym
        raise TypeError("Symbol '{}' is not a function".format(func_name))

    @property
    def function(self):
        return self.request.function

@rpc.task(name='celery_rpc.call', bind=True, base=FunctionTask)
def call(function, args, kwargs):
     """ Call function with args & kwargs

    :param function: full function name like 'package.module:function'
    :param args: positional parameters
    :param kwargs: named parameters
        {'id': 1, 'title': 'hello'} or [{'id': 1, 'title': 'hello'}]
    :return: result of function

    """