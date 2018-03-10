from setuptools import setup

setup(name='KubeYAML',
      version='0.1',
      description='Tool for altering Kubernetes YAML files',
      author='Michael Bridgen',
      author_email='mikeb@squaremobius.net',
      url='https://github.com/squaremo/kubeyaml',
      py_modules=['kubeyaml'],
      install_requires=['ruamel.yaml>=0.15']
)
