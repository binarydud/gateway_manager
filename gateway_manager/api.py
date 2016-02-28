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
