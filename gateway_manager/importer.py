#!/usr/bin/python
import re
import ramlfications
import boto3
import hashlib
import json
import argparse


def parse_annotations(resources):
    for resource in resources:
        if getattr(resource, 'method'):
            resource.handler = resource.raw[resource.method].get('(handlerArn)')
            resource.role = resource.raw[resource.method].get('(roleArn)')
            resource.is_valid_gateway = True
            for response in resource.responses:
                response.pattern = response.raw[response.code].get('(selectionPattern)', None)  # NOQA

    return resources


def get_api_by_name(client, name):
    resp = client.get_rest_apis()
    return next((x for x in resp['items'] if x['name'] == name), None)


def create_method(client, api_id, resource, http_method):
    method = http_method.upper()
    if resource.existing:
        return None
    return client.put_method(
        restApiId=api_id,
        resourceId=resource.aws_id,
        httpMethod=method,
        authorizationType="none"
    )


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
    resource_id,
    http_method,
    arn,
    role,
    content_type,
    update=False
):
    region = client._client_config.region_name
    mapping_templates = {}
    mapping_templates[content_type] = create_request_mapping_template()
    handler_template = 'arn:aws:apigateway:{}:lambda:path/2015-03-31/functions/{}/invocations'  # NOQA
    handler = handler_template.format(region, arn)
    if update:
        client.update_integration(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod=http_method,
            patchOperations=[
                {"op": "replace", "path": "/httpMethod", "value": "POST"},
                {"op": "replace", "path": "/uri", "value": handler},
            ]
        )
    else:
        client.put_integration(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod=http_method,
            type='AWS',
            # Lambda functions are always invoked via POST
            # http://docs.aws.amazon.com/lambda/latest/dg/API_Invoke.html
            integrationHttpMethod="POST",
            uri=handler,
            requestParameters={
            },
            requestTemplates=mapping_templates,
        )


def create_method_response(
    client,
    api_id,
    resource,
    http_method,
    status_code,
    bodies,
):
    if resource.existing:
        # this should eventually check the method response and patch
        return
    responseModels = {}
    for body in bodies:
        if status_code < 400:
            responseModels[body.mime_type] = "Empty"
        else:
            responseModels[body.mime_type] = "Error"
    return client.put_method_response(
        restApiId=api_id,
        resourceId=resource.aws_id,
        httpMethod=http_method,
        statusCode=str(status_code),
        responseParameters={
        },
        responseModels=responseModels
    )


def create_integration_response(
    client,
    api_id,
    resource_id,
    http_method,
    status_code,
    selection_pattern=None,
):
    params = dict(
        restApiId=api_id,
        resourceId=resource_id,
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
    return client.put_integration_response(
        **params
    )


def create_resource(client, api_id, root_id, resource):
    resource = create_resource_path(client, api_id, root_id, resource)
    if resource.method:
        http_method = resource.method.upper()
        if http_method:
            create_method(client, api_id, resource, http_method)
            # resaponse = resource.responses[0]
            # body = response.body[0]
            handler_arn = build_handler_arn(resource.handler)
            create_integration_request(
                client,
                api_id,
                resource.aws_id,
                http_method,
                handler_arn,
                resource.role,
                "application/json"
            )
            attach_handler_policy(client, api_id, handler_arn, resource.path, http_method)  # NOQA
            for response in resource.responses:
                create_method_response(
                    client,
                    api_id,
                    resource,
                    http_method,
                    response.code,
                    response.body
                )
                create_integration_response(
                    client,
                    api_id,
                    resource.aws_id,
                    http_method,
                    response.code,
                    selection_pattern=getattr(response, 'pattern', None)
                )
            """
            create_integration_request(
                client,
                api_id,
                resource.aws_id,
                http_method,
                resource.handler,
                resource.role,
                body.mime_type,
                True
            )
            """


def remove_prefix(prefix, path):
    if prefix and path.startswith(prefix):
        return path[len(prefix):]
    return path


def build_parent_path(resource):
    if resource.parent is None:
        return ''
    else:
        return resource.parent.path


def path_part(resource):
    parent_path = build_parent_path(resource)
    path_part = remove_prefix(parent_path, resource.path)
    path_part = remove_prefix('/', path_part)
    resource.path_part = path_part
    return resource


def create_resource_path(client, api_id, root_id, resource):
    parent_id = getattr(resource.parent, 'aws_id', None)
    if parent_id is None:
        parent_id = root_id
    params = {
        'restApiId': api_id,
        'pathPart': path_part(resource).path_part,
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


def transform_resources(resources):
    resources = map(path_part, resources)
    return resources


def associate_resources(aws_resources, raml_resources):
    lookup_table = {k['path']: k for k in aws_resources}
    for resource in raml_resources:
        if resource.path in lookup_table:
            resource.aws_id = lookup_table[resource.path]['id']
    return raml_resources


def grab_root_resource(aws_resources):
    return next((x for x in aws_resources if x['path'] == '/'), None)


def main(region, profile='default'):
    boto3.setup_default_session(profile_name=profile)
    client = boto3.client('apigateway', region_name=region)
    raml = ramlfications.parse('api_schema.raml')
    api_name = raml.title
    api = get_api_by_name(client, api_name)
    if api is None:
        api = client.create_rest_api(name=api_name)
    aws_resources = client.get_resources(restApiId=api['id'])['items']
    root = grab_root_resource(aws_resources)
    resource = api.transform_resources(raml.resource)
    # resources = parse_annotations(raml.resources)
    # resources = transform_resources(resources)
    resources = associate_resources(aws_resources, resources)

    for resource in resources:
        create_resource(client, api['id'], root['id'], resource)
    deployment = client.create_deployment(
        restApiId=api['id'],
        stageName=raml.base_uri
    )
    data = {
        'deployment': deployment['id'],
        'api': api['id'],
        'uri': 'https://{}.execute-api.{}.amazonaws.com/{}/'.format(
            api['id'],
            region,
            raml.base_uri
        )
    }
