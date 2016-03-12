from setuptools import setup, find_packages
INSTALL_REQUIRES = [
    'click==6.3',
    'ramlfications==0.1.9',
    'boto3==1.2.6',
    'Paste==2.0.2',
    'Routes==2.2',
    'WebOb==1.5.1',
]

setup(
    author="Matt George",
    author_email="mgeorge@gmail.com",
    name="gateway_manager",
    description="Manage your api gateway and lambda functions with ease.",
    packages=find_packages(exclude=['tests']),
    version='0.1.0',
    install_requires=INSTALL_REQUIRES,
    entry_points={
        'console_scripts': [
            'gateway_manage=gateway_manager.scripts:cli'
        ]
    }
)
