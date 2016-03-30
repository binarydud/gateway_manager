import click

from gateway_manager import (
    generate_function,
    devserver,
    importer
)

@click.group()
def cli():
    pass

@cli.command()
@click.argument('name')
@click.option('--description', default="")
@click.option('--memory', default=128, type=int)
@click.option('--timeout', default=5, type=int)
@click.option('--runtime', default='python', type=str)
def generate(name, description, memory, timeout, runtime):
    """Create a new lambda function."""
    generate_function.generate(name, description, memory, timeout, runtime)
    click.echo('finished generating %s' % name)


@cli.command('devserver')
def server():
    """Runs a local development version of your api gateway."""
    devserver.bootstrap()


@cli.command('importer')
@click.option('--region', default='us-east-1')
@click.option('--profile', default='default')
def importer_(region, profile):
    """Imports a raml spec into api gateway."""
    importer.main(region, profile)
