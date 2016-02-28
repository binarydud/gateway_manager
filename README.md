# API Gateway Manager

Gateway manager sits on top of [Apex](http://apex.run). The goal is to seamlessly
allow you to manage an aws api gateway with a raml specification and lambda functions.

Currently you can do the following:

- generate python lambda functions for apex to mange
- run a devserver that simulates an api gateway locally (currently python only)
- import a raml specification and create an api gateway from the specification
