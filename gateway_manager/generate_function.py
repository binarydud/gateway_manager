import os
import argparse
import json
_handler_template = """
def handle(event, context):
    return None
"""


def generate_file(path, content):
    if not os.path.exists(path):
        with open(path, 'wb') as f:
            f.write(content)


def generate(name, description, memory=128, timeout=5):
    function_path = 'functions/{}'.format(name)
    file_path = '{}/main.py'.format(function_path)
    config_path = '{}/function.json'.format(function_path)
    apex_ignore_path = '{}/.apexignore'.format(function_path)
    git_ignore_path = '{}/.gitignore'.format(function_path)
    config = dict(
        name=name,
        description=description,
        memory=memory,
        timeout=timeout,
        runtime='python',
        hooks={
            "build": "pip install -r requirements.txt -t ."
        },
        environment={},
    )

    config_template = json.dumps(config, indent=2)
    if not os.path.exists(function_path):
        os.makedirs(function_path)
    generate_file(file_path, _handler_template)
    generate_file(config_path, config_template)
    generate_file(apex_ignore_path, '*.dist-info/')
    generate_file(git_ignore_path, '*.dist-info/')
