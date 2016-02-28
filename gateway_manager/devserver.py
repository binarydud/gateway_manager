import imp
import ramlfications
import boto3
from wsgiref.simple_server import make_server
from pyramid.config import Configurator
from pyramid.response import Response
from gateway_manager import api

import imp, os.path
import re

# http://stackoverflow.com/questions/5362771/load-module-from-string-in-python
def _import(module_name):
    # http://code.activestate.com/recipes/159571-importing-any-file-without-modifying-syspath/
    filename = 'functions/{}/main.py'.format(module_name)
    (path, name) = os.path.split(filename)
    (name, ext) = os.path.splitext(name)

    (file, filename, data) = imp.find_module(name, [path])
    return imp.load_module(module_name, file, filename, data)


def run_function(request, name):
    module = _import(name)
    event = {
        "path" : {
            key: value for key, value in request.matchdict.items()
        },
        "querystring" : {
            key: value for key, value in request.GET.items()
        },
        "headers" : {
            key: value for key, value in request.params.items()
        },
        "body": request.body
    }
    return module.handle(event, {})

def _parse_response(response):
    response.pattern = response.raw[response.code].get('(selectionPattern)', None)  # NOQA
    return response


def _parse_resource(resource):
    if getattr(resource, 'method'):
        resource.handler = resource.raw[resource.method].get('(handler)')
        resource.responses = map(_parse_response, resource.responses)
    return resource


def parse_annotations(resources):
    return map(_parse_resource, resources)


def build_parent_path(resource):
    if resource.parent is None:
        return ''
    else:
        return resource.parent.path


def remove_prefix(prefix, path):
    if prefix and path.startswith(prefix):
        return path[len(prefix):]
    return path


def path_part(resource):
    parent_path = build_parent_path(resource)
    path_part = remove_prefix(parent_path, resource.path)
    path_part = remove_prefix('/', path_part)
    resource.path_part = path_part
    return resource


def transform_resources(resources):
    resources = parse_annotations(resources)
    return map(path_part, resources)


def _filter_absent_methods(resource):
    return getattr(resource, 'method') is not None


def _call_apex(request):
    print ('Incoming request')
    node = _lookup_table[request.matched_route.name]
    function_name = node.handler
    try:
        response = run_function(request, function_name)
    except Exception as e:
        print node.responses
        print repr(e)
        # re.search(function_details, repr(e))
    return Response('{} {}!'.format(request.method, request.matchdict))


def add_resource(config, resource):
    route_name = '{}-{}'.format(
        resource.path,
        resource.method,
    )
    config.add_route(route_name, resource.path, request_method=resource.method.upper())
    config.add_view(
        _call_apex,
        route_name=route_name,
    )


def build_server(resources):
    config = Configurator()
    [add_resource(config, node) for node in resources]
    app = config.make_wsgi_app()
    return app

_lookup_table = {}


def build_lookup_table(resources):
    for node in resources:
        route_name = '{}-{}'.format(
            node.path,
            node.method,
        )
        _lookup_table[route_name] = node

def bootstrap():
    # read raml, create router
    raml = ramlfications.parse('api_schema.raml')
    # resources = transform_resources(raml.resources)
    resource = api.transform_resources(raml.resources)
    resources = filter(_filter_absent_methods, resources)
    build_lookup_table(resources)
    print(list(resources))
    for node in resources:
        print(node.path)
    app = build_server(resources)
    server = make_server('0.0.0.0', 8080, app)
    server.serve_forever()
