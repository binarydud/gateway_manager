from setuptools import setup, find_packages
INSTALL_REQUIRES = [
    'pyramid',
    'boto3',
    'ramlfications',
    'jinja2',
    'paste'
]


TESTS_REQUIRE = [
    'webtest'
]
setup(
    author="Matt George",
    author_email="mgeorge@gmail.com",
    name="gateway_manager",
    description="Manage your api gateway and lambda functions with ease.",
    packages=find_packages(exclude=['tests']),
    version='0.1.0',
    entry_points={
        'console_scripts': [
            'gateway_manage=gateway_manager.scripts:cli'
        ]
    }
)
