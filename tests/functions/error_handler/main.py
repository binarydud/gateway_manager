class NotFound(Exception):
    pass

def handle(event, context):
    if event['querystring'].get('error'):
        raise Exception('this is a 500 level error message')
    raise NotFound('This is a 400 level error message')
