import functools


def _parse_response(response):
    if response.raw[response.code]:
        response.pattern = response.raw[response.code].get('(selectionPattern)', None)  # NOQA
    return response


def _parse_resource(root, resource):
    root_role = root.raw.get('(iam_role)', None)
    if getattr(resource, 'method'):
        resource.handler = resource.raw[resource.method].get('(handler)')
        resource.endpoint = resource.raw[resource.method].get('(ednpoint)')
        resource.iam_role = resource.raw[resource.method].get(
            '(iam_role)',
            root_role
        )
        resource.responses = map(_parse_response, resource.responses)
    return resource


def parse_annotations(root, resources):
    parse_resource = functools.partial(_parse_resource, root)
    return map(parse_resource, resources)


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


def transform_resources(root, resources):
    resources = parse_annotations(root, resources)
    return map(path_part, resources)
