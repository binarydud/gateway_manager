#!/usr/bin/python
import re
import ramlfications
import boto3
import hashlib
import json
import functools
from botocore import exceptions
from gateway_manager import api
from gateway_manager.gateway import ApiGateway


def get_handler_arn(project_name, hander_name):
    client = boto3.client('lambda')
    response = client.get_function(FunctionName='{}_{}'.format(
        project_name,
        hander_name
    ))
    return response['Configuration']['FunctionArn']


def get_api_by_name(client, name):
    resp = client.get_rest_apis()
    return next((x for x in resp['items'] if x['name'] == name), None)


def create_method(client, api_id, resource, http_method, authorizers):
    print 'Creating Method {} for {}'.format(http_method, resource.name)
    method = http_method.upper()
    kwargs = dict(
        restApiId=api_id,
        resourceId=resource.aws_id,
        httpMethod=method,
        apiKeyRequired=False,
    )
    if hasattr(resource, 'secured_by') and resource.secured_by:
        print 'resource is secured'
        authorizer = next(
            iter(filter(lambda x: x.name == resource.secured_by, authorizers)), None  # NOQA
        )
        kwargs['authorizationType'] = "CUSTOM"
        kwargs['authorizerId'] = authorizer.authorizer_id
        # attach_handler_policy(client, api_id, authorizer.arn, resource.path, http_method)  # NOQA

    else:
        kwargs['authorizationType'] = "none"

    print kwargs
    return client.put_method(**kwargs)


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


def create_error_response_template(code):
    template = """
{
"message" : "$input.path('$.errorMessage')",
"code": %s
}
""" % code
    return template
# https://github.com/awslabs/aws-apigateway-importer/issues/9


def attach_handler_policy(client, api_id, arn, path, method):
    path = re.sub('({\w+})', '*', path)
    path = path.split('/')
    path = '/'.join(path)

    region = client._client_config.region_name
    lamdba_client = boto3.client('lambda', region_name=region)
    statement_id = hashlib.sha1(
        arn+api_id+path+method
    ).hexdigest()
    account_id = arn.split(':')[4]
    source = 'arn:aws:execute-api:{}:{}:{}/*/{}{}'.format(
        region,
        account_id,
        api_id,
        method,
        path
    )

    p = {}
    try:
        p = lamdba_client.get_policy(FunctionName=arn)
    except Exception:
        current = False
    policy = json.loads(p.get('Policy', "{}"))
    current = next((p['Sid'] for p in policy.get('Statement', []) if p['Sid'] == statement_id), None)  # NOQA
    if current:
        lamdba_client.remove_permission(
            FunctionName=arn,
            StatementId=statement_id,
        )

    lamdba_client.add_permission(
        FunctionName=arn,
        StatementId=statement_id,
        Action='lambda:InvokeFunction',
        Principal='apigateway.amazonaws.com',
        SourceArn=source,
    )


def create_integration_request(
    client,
    api_id,
    resource,
    http_method,
    arn,
    update=False
):
    region = client._client_config.region_name
    template_mapping = create_request_mapping_template()
    mapping_templates = {
        'application/json': template_mapping,
        'application/x-www-form-urlencoded': template_mapping
    }
    handler_template = 'arn:aws:apigateway:{}:lambda:path/2015-03-31/functions/{}/invocations'  # NOQA
    handler = handler_template.format(region, arn)
    method = http_method.strip().upper()
    has_role = hasattr(resource, 'iam_role') and resource.iam_role
    if update:
        client.update_integration(
            restApiId=api_id,
            resourceId=resource.aws_id,
            httpMethod=method,
            patchOperations=[
                {"op": "replace", "path": "/httpMethod", "value": "POST"},
                {"op": "replace", "path": "/uri", "value": handler},
            ]
        )
    else:
        kwargs = dict(
            restApiId=api_id,
            resourceId=resource.aws_id,
            httpMethod=method,
            type='AWS',
            # Lambda functions are always invoked via POST
            # http://docs.aws.amazon.com/lambda/latest/dg/API_Invoke.html
            integrationHttpMethod='POST',
            uri=handler,
            requestParameters={},
            requestTemplates=mapping_templates,
        )
        if has_role:
            kwargs['credentials'] = resource.iam_role
        client.put_integration(**kwargs)
    if not has_role:
        attach_handler_policy(client, api_id, arn, resource.path, http_method)  # NOQA


def create_method_response(
    client,
    api_id,
    resource,
    http_method,
    status_code,
    bodies,
    headers=[],
):
    origin = resource.raw.get('(cors)', {}).get('origin')
    responseModels = {}
    responseParameters = {}
    for body in bodies:
        if status_code < 400:
            responseModels[body.mime_type] = "Empty"
        else:
            responseModels[body.mime_type] = "Error"
    for header in headers:
        responseParameters['method.response.header.{}'.format(header.name)] = True  # NOQA
    if origin:
        responseParameters['method.response.header.Access-Control-Allow-Origin'] = True  # NOQA
    return client.put_method_response(
        restApiId=api_id,
        resourceId=resource.aws_id,
        httpMethod=http_method,
        statusCode=str(status_code),
        responseParameters=responseParameters,
        responseModels=responseModels
    )


def create_integration_response(
    client,
    api_id,
    resource,
    http_method,
    status_code,
    selection_pattern=None,
    headers=[]
):
    origin = resource.raw.get('(cors)', {}).get('origin')
    params = dict(
        restApiId=api_id,
        resourceId=resource.aws_id,
        httpMethod=http_method,
        statusCode=str(status_code),
    )
    if selection_pattern:
        params['selectionPattern'] = selection_pattern
    else:
        params['selectionPattern'] = ""
    if selection_pattern and status_code >= 400:
        params['responseTemplates'] = {
            "application/json": create_error_response_template(status_code)
        }
    response_params = {}
    for header in headers:
        key = header.name
        value = header.raw.get(key, '')
        response_params['method.response.header.{}'.format(key)] = value
    if origin:
        response_params['method.response.header.Access-Control-Allow-Origin'] = "'{}'".format(origin)  # NOQA
    params['responseParameters'] = response_params
    return client.put_integration_response(
        **params
    )


def create_cors(client, api_id, resource):
    try:
        client.delete_method(
            restApiId=api_id,
            resourceId=resource.aws_id,
            httpMethod='OPTIONS'
        )
    except exceptions.ClientError as e:
        if e.response['Error']['Code'] != 'NotFoundException':
            raise e
    client.put_method(
        restApiId=api_id,
        resourceId=resource.aws_id,
        httpMethod='OPTIONS',
        apiKeyRequired=False,
        authorizationType="none",
    )
    cors_data = resource.raw.get('(cors)', {})
    client.put_method_response(
        restApiId=api_id,
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
    client.put_integration(
        restApiId=api_id,
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
    client.put_integration_response(
        restApiId=api_id,
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


def create_resource(client, api_id, root_id, resource, project_name, authorizers):  # NOQA
    resource = create_resource_path(client, api_id, root_id, resource)
    if resource.raw.get('(cors)'):
        create_cors(client, api_id, resource)
    if resource.method:
        http_method = resource.method.upper()
        if http_method:
            try:
                client.delete_method(
                    restApiId=api_id,
                    resourceId=resource.aws_id,
                    httpMethod=http_method
                )
            except exceptions.ClientError as e:
                if e.response['Error']['Code'] != 'NotFoundException':
                    raise e
            method = create_method(client, api_id, resource, http_method, authorizers)
            # resaponse = resource.responses[0]
            # body = response.body[0]
            handler_arn = get_handler_arn(project_name, resource.handler)
            create_integration_request(
                client,
                api_id,
                resource,
                http_method,
                handler_arn,
            )
            for response in resource.responses:
                headers = response.headers or []
                bodies = response.body or []
                create_method_response(
                    client,
                    api_id,
                    resource,
                    http_method,
                    response.code,
                    bodies,
                    headers=headers,
                )
                create_integration_response(
                    client,
                    api_id,
                    resource,
                    http_method,
                    response.code,
                    selection_pattern=getattr(response, 'pattern', None),
                    headers=headers,
                )


def create_resource_path(client, api_id, root_id, resource):
    parent_id = getattr(resource.parent, 'aws_id', None)
    if parent_id is None:
        parent_id = root_id
    params = {
        'restApiId': api_id,
        'pathPart': api.path_part(resource).path_part,
        'parentId': parent_id
    }
    if getattr(resource, 'aws_id', None):
        resource.existing = True
    else:
        aws_resource = client.create_resource(
            **params
        )
        resource.aws_id = aws_resource['id']
        resource.existing = False
    return resource


def associate_resources(aws_resources, raml_resources):
    lookup_table = {k['path']: k for k in aws_resources}
    for resource in raml_resources:
        if resource.path in lookup_table:
            resource.aws_id = lookup_table[resource.path]['id']
    return raml_resources


def grab_root_resource(aws_resources):
    return next((x for x in aws_resources if x['path'] == '/'), None)


def create_security_scheme(client, api_id, project_name, scheme):
    arn = get_handler_arn(project_name, scheme.settings['handler'])
    scheme.arn = arn
    region = client._client_config.region_name
    handler_template = 'arn:aws:apigateway:{}:lambda:path/2015-03-31/functions/{}/invocations'  # NOQA
    uri = handler_template.format(region, arn)
    if hasattr(scheme, 'authorizer_id') and scheme.authorizer_id:
        client.update_authorizer(
            restApiId=api_id,
            authorizerId=scheme.authorizer_id,
            patchOperations=[
                {"op": "replace", "path": "/authorizerCredentials", "value": scheme.settings['iam_role']},
                {"op": "replace", "path": "/identitySource", "value": scheme.settings['token_source']},
                {"op": "replace", "path": "/authorizerUri", "value": uri},
            ]
        )
    else:
        response = client.create_authorizer(
            restApiId=api_id,
            name=scheme.name,
            type='TOKEN',
            authorizerUri=uri,
            authorizerCredentials=scheme.iam_role,
            identitySource=scheme.settings['token_source'],
        )
        scheme.authorizer_id = response['id']
    return scheme


def associate_authorizers(aws_authorizers, security_schemes):
    for scheme in security_schemes:
        authorizer = next(
            iter(filter(lambda x: x['name'] == scheme.name, aws_authorizers)), None  # NOQA
        )
        if authorizer:
            scheme.authorizer_id = authorizer['id']
    return security_schemes


def main(region, profile='default'):
    session = boto3.Session(
        profile_name=profile,
        region_name=region
    )
    apex_json = json.load(open('project.json'))
    raml = ramlfications.parse('api_schema.raml')
    gateway = ApiGateway(raml, apex_json, session)
    print 'Creating Api Gateway'
    gateway.create()
    gateway.load()
    print 'Creating Authorizers'
    gateway.create_authorizers()
    print 'Creating Resources'
    gateway.create_resources()
    print 'Deploying Stage'
    print gateway.create_deployment()


def old(region, profile='default'):
    project_details = json.load(open('project.json'))
    boto3.setup_default_session(
        profile_name=profile,
        region_name=region
    )
    client = boto3.client('apigateway', region_name=region)
    raml = ramlfications.parse('api_schema.raml')
    api_name = raml.title
    api_gateway = get_api_by_name(client, api_name)
    if api_gateway is None:
        api_gateway = client.create_rest_api(name=api_name)
    aws_resources = client.get_resources(restApiId=api_gateway['id'])['items']
    root = grab_root_resource(aws_resources)
    resources = api.transform_resources(raml, raml.resources)
    resources = associate_resources(aws_resources, resources)
    aws_authorizers = client.get_authorizers(restApiId=api_gateway['id'])['items']  # NOQA
    authorizers = associate_authorizers(aws_authorizers, raml.security_schemes or [])  # NOQA
    create_authorizer = functools.partial(
        create_security_scheme,
        client,
        api_gateway['id'],
        project_details['name']
    )
    authorizers = map(create_authorizer, authorizers)

    for resource in resources:
        print 'Creating Resource'
        create_resource(
            client,
            api_gateway['id'],
            root['id'],
            resource,
            project_details['name'],
            authorizers
        )
    deployment = client.create_deployment(
        restApiId=api_gateway['id'],
        stageName=raml.base_uri
    )
    data = {
        'deployment': deployment['id'],
        'api': api_gateway['id'],
        'uri': 'https://{}.execute-api.{}.amazonaws.com/{}/'.format(
            api_gateway['id'],
            region,
            raml.base_uri
        )
    }
    print data
