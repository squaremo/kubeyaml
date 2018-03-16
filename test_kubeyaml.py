import kubeyaml

from hypothesis import given, strategies as strats
from hypothesis.strategies import composite

dns_labels = strats.from_regex(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$").map(lambda s: s.strip())

# Excluding digests for now

hostcomponents = strats.from_regex(r"^([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])$").map(lambda s: s.strip())
portnumbers = strats.integers(min_value=1, max_value=32767)

hostname_without_ports = strats.builds(lambda cs: '.'.join(cs), strats.lists(elements=hostcomponents, min_size=1, average_size=3))
hostnames = strats.one_of(hostname_without_ports, strats.builds(lambda h, p: h + ':' + str(p), hostname_without_ports, portnumbers))

kinds = strats.sampled_from(['Deployment', 'DaemonSet', 'StatefulSet', 'CronJob'])

def manifest(kind, name, namespace):
    metadata = dict()
    metadata['name'] = name
    if namespace != '':
        metadata['namespace'] = namespace
    return {
        'kind': kind,
        'metadata': metadata,
    }

manifests = strats.builds(manifest, kind=kinds, name=dns_labels, namespace=strats.one_of(strats.just(''), dns_labels))

class Spec:
    def __repr__(self):
        return "Spec(kind=%s,name=%s,namespace=%s)" % (self.kind, self.name, self.namespace)

@given(manifests)
def test_match_self(man):
    spec = Spec()
    spec.kind=man['kind']
    spec.name=man['metadata']['name']
    spec.namespace=man['metadata'].get('namespace', 'default')
    assert kubeyaml.match_manifest(spec, man)
