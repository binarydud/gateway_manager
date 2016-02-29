import os
import argparse
import json
_handler_template = """
def handle(event, context):
    return None
"""

def generate(name, description, memory=128, timeout=5):
    function_path = 'functions/{}'.format(name)
    file_path = '{}/main.py'.format(function_path)
    config_path = '{}/function.json'.format(function_path)
    config = dict(
        name=name,
        description=description,
        memory=memory,
        timeout=timeout,
        runtime='python'
    )

    config_template = json.dumps(config, indent=2)
    if not os.path.exists(function_path):
        os.makedirs(function_path)
    if not os.path.exists(file_path):
        with open(file_path, 'wb') as f:
            f.write(_handler_template)
    if not os.path.exists(config_path):
        with open(config_path, 'wb') as f:
            f.write(config_template)
