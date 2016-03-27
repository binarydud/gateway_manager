import imp
import functools
import json
import ramlfications
import boto3
import logging
import routes
from wsgiref.simple_server import make_server
from webob.dec import wsgify
from webob import Response
from paste.translogger import TransLogger

from gateway_manager import api

import imp, os.path
import re
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter(
        '%(asctime)s:%(levelname)-2s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

class WsgiApp(object):
    def __init__(self):
        self.map = routes.Mapper()

    def add_view(self, pattern, function, method='GET'):
        self.map.connect(
            None,
            pattern,
            function=function,
            conditions=dict(method=[method])
        )

    @wsgify
    def __call__(self, request):
        result = self.map.routematch(environ=request.environ)
        if not result:
            # TODO - return actual response
            return 404
        match, route = result
        results = match.copy()
        name = results.pop('function')
        ret = self.run_function(request, name, results)
        return Response(body=json.dumps(ret), content_type='application/json')


    def run_function(self, request, name, params):
        module = import_lambda_function(name)
        event = {
            "path" : {
                key: value for key, value in params.items()
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

# http://stackoverflow.com/questions/5362771/load-module-from-string-in-python
def import_lambda_function(module_name):
    # http://code.activestate.com/recipes/159571-importing-any-file-without-modifying-syspath/
    filename = 'functions/{}/main.py'.format(module_name)
    (path, name) = os.path.split(filename)
    (name, ext) = os.path.splitext(name)

    (file, filename, data) = imp.find_module(name, [path])
    return imp.load_module(module_name, file, filename, data)


def build_error_response(code, error):
    return json.dumps({
        "message": error.message,
        "code": code
    })


def run_function(request, name):
    module = import_lambda_function(name)
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


def filter_absent_method(resource):
    return getattr(resource, 'method') is not None


def filter_on_response_pattern(error, response):
    if getattr(response, 'pattern', None) is None:
        return False
    return re.search(response.pattern, repr(error))


def _call_apex(request):
    node = _lookup_table[request.matched_route.name]
    function_name = node.handler
    try:
        response = run_function(request, function_name)
    except Exception as e:
        partial = functools.partial(filter_on_response_pattern, e)
        response = filter(partial, node.responses)
        if response:
            response = response[0]
            body = build_error_response(response.code, e)

            return Response(body=body, status_code=response.code, content_type=response.body[0].mime_type)
        return e
    else:
        return Response(body=json.dumps(response), content_type='application/json')


def add_resource(app, resource):
    app.add_view(resource.path, resource.handler, method=resource.method.upper())


def build_server(resources):
    app = WsgiApp()
    [add_resource(app, node) for node in resources]
    return app


def build_wsgi_app(raml_file):
    # read raml, create router
    raml = ramlfications.parse(raml_file)
    resources = api.transform_resources(raml, raml.resources)
    resources = filter(filter_absent_method, resources)
    app = build_server(resources)
    print app.map
    app = TransLogger(app, setup_console_handler=False)
    return app


def bootstrap(raml_file='api_schema.raml'):
    logger.info('Creating development server')
    app = build_wsgi_app(raml_file)
    logger.info('Starting server on port 8080')
    server = make_server('0.0.0.0', 8080, app)
    server.serve_forever()
