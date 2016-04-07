import boto3
from botocore import exceptions
from gateway_manager import api


def create_error_response_template(code):
    template = """
{
"message" : "$input.path('$.errorMessage')",
"code": %s
}
""" % code
    return template


def create_request_mapping_template():
    mapping_template = """
#set($params = $input.params())
#set($path = $params.path)
#set($querystring = $params.querystring)
#set($headers = $params.header)
#set($body = $input.json('$'))
{
"path" : {
    #foreach ($mapEntry in $path.entrySet())
        "$mapEntry.key":"$mapEntry.value"
        #if($velocityCount < $path.size()),#end
    #end
    },
"querystring" : {
    #foreach ($mapEntry in $querystring.entrySet())
        "$mapEntry.key":"$mapEntry.value"
        #if($velocityCount < $querystring.size()),#end
    #end
    },
"headers" : {
    #foreach ($mapEntry in $headers.entrySet())
        "$mapEntry.key":"$mapEntry.value"
        #if($velocityCount < $headers.size()),#end
    #end
    }
#if($body),
"body": $body
#end
}
"""
    return mapping_template


def get_handler_arn(client, project_name, hander_name):
    response = client.get_function(FunctionName='{}_{}'.format(
        project_name,
        hander_name
    ))
    return response['Configuration']['FunctionArn']


def grab_root_resource(aws_resources):
    return next((x for x in aws_resources if x['path'] == '/'), None)


def associate_resources(aws_resources, raml_resources):
    lookup_table = {k['path']: k for k in aws_resources}
    for resource in raml_resources:
        if resource.path in lookup_table:
            resource.aws_id = lookup_table[resource.path]['id']
    return raml_resources


def associate_authorizers(aws_authorizers, security_schemes):
    for scheme in security_schemes:
        authorizer = next(
            iter(filter(lambda x: x['name'] == scheme.name, aws_authorizers)), None  # NOQA
        )
        if authorizer:
            scheme.authorizer_id = authorizer['id']
    return security_schemes


class ApiGateway(object):
    def __init__(self, raml, apex_json, session):
        self.raml = raml
        self.name = raml.title
        self.session = session
        self.client = session.client('apigateway')
        self.lambda_client = session.client('lambda')
        self.apex_details = apex_json
        self.region = self.session.region_name

    def apex_name(self):
        return self.apex_details['name']

    def get_by_name(self):
        resp = self.client.get_rest_apis()
        return next((x for x in resp['items'] if x['name'] == self.name), None)

    def create(self):
        self.api_gateway = (
            self.get_by_name() or self.client.create_rest_api(name=self.name)
        )
        self.id = self.api_gateway['id']

    def load(self):
        aws_resources = self.client.get_resources(restApiId=self.id)['items']
        self.root_node = grab_root_resource(aws_resources)
        resources = api.transform_resources(self.raml, self.raml.resources)
        self.resources = associate_resources(aws_resources, resources)
        aws_authorizers = self.client.get_authorizers(restApiId=self.id)['items']  # NOQA
        self.authorizers = associate_authorizers(
            aws_authorizers,
            self.raml.security_schemes or []
        )

    def create_security_scheme(self, scheme):
        arn = get_handler_arn(self.lambda_client, self.apex_name(), scheme.settings['handler'])
        scheme.arn = arn
        region = self.region
        handler_template = 'arn:aws:apigateway:{}:lambda:path/2015-03-31/functions/{}/invocations'  # NOQA
        uri = handler_template.format(region, arn)
        if hasattr(scheme, 'authorizer_id') and scheme.authorizer_id:
            self.client.update_authorizer(
                restApiId=self.id,
                authorizerId=scheme.authorizer_id,
                patchOperations=[
                    {"op": "replace", "path": "/authorizerCredentials", "value": scheme.settings['iam_role']},  # NOQA
                    {"op": "replace", "path": "/identitySource", "value": scheme.settings['token_source']},  # NOQA
                    {"op": "replace", "path": "/authorizerUri", "value": uri},
                ]
            )
        else:
            response = self.client.create_authorizer(
                restApiId=self.id,
                name=scheme.name,
                type='TOKEN',
                authorizerUri=uri,
                authorizerCredentials=scheme.iam_role,
                identitySource=scheme.settings['token_source'],
            )
            scheme.authorizer_id = response['id']
        return scheme

    def create_authorizers(self):
        self.authorizers = map(self.create_security_scheme, self.authorizers)

    def create_resource_path(self, resource):
        parent_id = getattr(resource.parent, 'aws_id', None)
        if parent_id is None:
            parent_id = self.root_node['id']
        params = {
            'restApiId': self.id,
            'pathPart': api.path_part(resource).path_part,
            'parentId': parent_id
        }
        if getattr(resource, 'aws_id', None):
            resource.existing = True
        else:
            aws_resource = self.client.create_resource(
                **params
            )
            resource.aws_id = aws_resource['id']
            resource.existing = False
        return resource

    def create_cors(self, resource):
        cors_data = resource.raw.get('(cors)', {})
        if not cors_data:
            return
        try:
            self.client.delete_method(
                restApiId=self.id,
                resourceId=resource.aws_id,
                httpMethod='OPTIONS'
            )
        except exceptions.ClientError as e:
            if e.response['Error']['Code'] != 'NotFoundException':
                raise e
        self.client.put_method(
            restApiId=self.id,
            resourceId=resource.aws_id,
            httpMethod='OPTIONS',
            apiKeyRequired=False,
            authorizationType="none",
        )
        cors_data = resource.raw.get('(cors)', {})
        self.client.put_method_response(
            restApiId=self.id,
            resourceId=resource.aws_id,
            httpMethod='OPTIONS',
            statusCode="200",
            responseParameters={
                'method.response.header.Access-Control-Allow-Headers': True,
                'method.response.header.Access-Control-Allow-Methods': True,
                'method.response.header.Access-Control-Allow-Origin': True,
            },
            responseModels={
                'application/json': 'Empty'
            }
        )
        self.client.put_integration(
            restApiId=self.id,
            resourceId=resource.aws_id,
            httpMethod='OPTIONS',
            type='MOCK',
            # Lambda functions are always invoked via POST
            # http://docs.aws.amazon.com/lambda/latest/dg/API_Invoke.html
            requestParameters={},
            requestTemplates={
                'application/json': '{"statusCode": 200}'
            },
        )
        print cors_data
        self.client.put_integration_response(
            restApiId=self.id,
            resourceId=resource.aws_id,
            httpMethod='OPTIONS',
            statusCode='200',
            selectionPattern="",
            responseTemplates={},
            responseParameters={
                'method.response.header.Access-Control-Allow-Headers': "'{}'".format(cors_data['headers']),  # NOQA
                'method.response.header.Access-Control-Allow-Methods': "'{}'".format(cors_data['methods']),  # NOQA
                'method.response.header.Access-Control-Allow-Origin': "'{}'".format(cors_data['origin']),  # NOQA
            },
        )

    def find_authorizer(self, name):
        return next(
            iter(filter(lambda x: x.name == name, self.authorizers)), None
        )

    def _create_method(self, resource):
        http_method = resource.method.strip().upper()
        method = http_method.upper()
        kwargs = dict(
            restApiId=self.id,
            resourceId=resource.aws_id,
            httpMethod=method,
            apiKeyRequired=False,
        )
        if hasattr(resource, 'secured_by') and resource.secured_by:
            print 'resource is secured'
            authorizer = self.find_authorizer(resource.secured_by)
            kwargs['authorizationType'] = "CUSTOM"
            kwargs['authorizerId'] = authorizer.authorizer_id
        else:
            kwargs['authorizationType'] = "none"

        return self.client.put_method(**kwargs)

    def _create_integration_request(self, resource):
        http_method = resource.method.strip().upper()
        template_mapping = create_request_mapping_template()
        mapping_templates = {
            'application/json': template_mapping,
            'application/x-www-form-urlencoded': template_mapping
        }
        has_role = hasattr(resource, 'iam_role') and resource.iam_role
        kwargs = dict(
            restApiId=self.id,
            resourceId=resource.aws_id,
            httpMethod=http_method,
            requestParameters={},
        )
        if resource.handler:
            handler_arn = get_handler_arn(self.lambda_client, self.name, resource.handler)
            handler_template = 'arn:aws:apigateway:{}:lambda:path/2015-03-31/functions/{}/invocations'  # NOQA
            kwargs['uri'] = handler_template.format(self.region, handler_arn)
            kwargs['integrationHttpMethod'] = "POST"
            kwargs['requestTemplates'] = mapping_templates
            kwargs['type'] = 'AWS'
            if has_role:
                kwargs['credentials'] = resource.iam_role
        else:
            kwargs['type'] = 'HTTP'
            kwargs['uri'] = resource.endpoint
            kwargs['integrationHttpMethod'] = http_method

        self.client.put_integration(**kwargs)
        # if resource.handler and not has_role:
        #    self.attach_handler_policy(arn, resource)

    def setup_method(self, resource):
        self.delete_method(resource)
        # create method
        self._create_method(resource)
        # create integration request
        self._create_integration_request(resource)
        # create method response
        for response in resource.responses:
            self.setup_response(resource, response)

    def create_method_response(self, resource, response):
        http_method = resource.method.strip().upper()
        headers = response.headers or []
        bodies = response.body or []
        origin = resource.raw.get('(cors)', {}).get('origin')
        responseModels = {}
        responseParameters = {}
        for body in bodies:
            if response.code < 400:
                responseModels[body.mime_type] = "Empty"
            else:
                responseModels[body.mime_type] = "Error"
        for header in headers:
            responseParameters['method.response.header.{}'.format(header.name)] = True  # NOQA

        if origin:
            responseParameters['method.response.header.Access-Control-Allow-Origin'] = True  # NOQA

        return self.client.put_method_response(
            restApiId=self.id,
            resourceId=resource.aws_id,
            httpMethod=http_method,
            statusCode=str(response.code),
            responseParameters=responseParameters,
            responseModels=responseModels
        )

    def create_integration_response(self, resource, response):
        headers = response.headers or []
        http_method = resource.method.strip().upper()
        origin = resource.raw.get('(cors)', {}).get('origin')
        selection_pattern = getattr(response, 'pattern', None)
        params = dict(
            restApiId=self.id,
            resourceId=resource.aws_id,
            httpMethod=http_method,
            statusCode=str(response.code),
        )
        if selection_pattern:
            params['selectionPattern'] = selection_pattern
        else:
            params['selectionPattern'] = ""
        if selection_pattern and response.code >= 400:
            params['responseTemplates'] = {
                "application/json": create_error_response_template(response.code)  # NOQA
            }
        response_params = {}
        for header in headers:
            key = header.name
            value = header.raw.get(key, '')
            response_params['method.response.header.{}'.format(key)] = value
        if origin:
            response_params['method.response.header.Access-Control-Allow-Origin'] = "'{}'".format(origin)  # NOQA
        params['responseParameters'] = response_params
        return self.client.put_integration_response(
            **params
        )

    def setup_response(self, resource, response):
        self.create_method_response(
            resource, response
        )
        self.create_integration_response(
            resource,
            response
        )

    def delete_method(self, resource):
        http_method = resource.method.strip().upper()
        try:
            self.client.delete_method(
                restApiId=self.id,
                resourceId=resource.aws_id,
                httpMethod=http_method
            )
        except exceptions.ClientError as e:
            if e.response['Error']['Code'] != 'NotFoundException':
                raise e

    def create_resources(self):
        return map(self.create_resource, self.resources)

    def create_resource(self, resource):
        # create resource path
        self.create_resource_path(resource)
        self.create_cors(resource)

        if resource.method:
            self.setup_method(resource)

    def create_deployment(self):
        base_uri = self.raml.base_uri
        deployment = self.client.create_deployment(
            restApiId=self.id,
            stageName=base_uri
        )
        data = {
            'deployment': deployment['id'],
            'api': self.id,
            'uri': 'https://{}.execute-api.{}.amazonaws.com/{}/'.format(
                self.id,
                self.region,
                base_uri
            )
        }
        return data
