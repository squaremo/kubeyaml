import kubeyaml

from hypothesis import given, assume, strategies as strats
from hypothesis.strategies import composite

def strip(s):
    return s.strip()

# Names of things in Kubernetes are generally DNS labels.
# https://github.com/kubernetes/community/blob/master/contributors/design-proposals/architecture/identifiers.md
dns_labels = strats.from_regex(
    r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$").map(strip)

# Image names (excluding digests for now)
# https://github.com/docker/distribution/blob/docker/1.13/reference/reference.go

host_components = strats.from_regex(r"^([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])$").map(strip)
port_numbers = strats.integers(min_value=1, max_value=32767)

hostnames_without_port = strats.builds(
    lambda cs: '.'.join(cs),
    strats.lists(elements=host_components, min_size=1, average_size=3))

hostnames_with_port = strats.builds(
    lambda h, p: h + ':' + str(p),
    hostnames_without_port, port_numbers)

hostnames = strats.one_of(hostnames_without_port, hostnames_with_port)

alphanumerics = strats.characters(min_codepoint=48, max_codepoint=122,
                                  whitelist_categories=['Ll', 'Lu', 'Nd'])
image_separators = strats.one_of(
    strats.just('.'), strats.just('_'), strats.just('__'),
    strats.integers(min_value=1, max_value=5).map(lambda n: '-' * n))

image_tags = strats.from_regex(r"^[\w][\w.-]{0,127}$").map(strip)

@composite
def image_components(draw):
    bits = draw(strats.lists(
        elements=strats.text(alphabet=alphanumerics, min_size=1),
        min_size=1, max_size=255))
    s = bits[0]
    for c in bits[1:]:
        sep = draw(image_separators)
        s = s + sep + c
    return s

def image_names():
    return strats.builds(lambda cs: '/'.join(cs),
                         strats.lists(elements=image_components(),
                                      min_size=1, average_size=3))

images_with_tag = strats.builds(
    lambda name, tag: name + ':' + tag,
    image_names(), image_tags)

# Kubernetes manifests
# https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.9/

kinds = strats.sampled_from(['Deployment', 'DaemonSet', 'StatefulSet', 'CronJob'])
namespaces = strats.one_of(strats.just(''), dns_labels)

def Container(name, image):
    # TODO(michael): more to go here
    return dict(name=name, image=image)

@composite
def manifests(draw):
    kind, name, namespace = draw(kinds), draw(dns_labels), draw(namespaces)
    metadata = dict()
    metadata['name'] = name
    if namespace != '':
        metadata['namespace'] = namespace

    containers = draw(strats.lists(
        elements=strats.builds(Container, dns_labels, images_with_tag)))
    podtemplate = {'template': {'spec': {'containers': containers}}}

    base = {
        'kind': kind,
        'metadata': metadata,
    }

    if kind == 'CronJob':
        base['spec'] = {'jobTemplate': {'spec': podtemplate}}
    else:
        base['spec'] = podtemplate

    return base

class Spec:
    def __repr__(self):
        return "Spec(kind=%s,name=%s,namespace=%s)" % (self.kind, self.name, self.namespace)

@given(manifests())
def test_match_self(man):
    spec = Spec()
    spec.kind=man['kind']
    spec.name=man['metadata']['name']
    # The namespace is always given in the spec
    spec.namespace=man['metadata'].get('namespace', 'default')
    assert kubeyaml.match_manifest(spec, man)

@given(manifests(), strats.data())
def test_find_container(man, data):
    spec = Spec()
    spec.kind=man['kind']
    spec.name=man['metadata']['name']
    # The namespace is always given in the spec
    spec.namespace=man['metadata'].get('namespace', 'default')

    cs = kubeyaml.containers(man)
    assume(len(cs) > 0)

    ind = data.draw(strats.integers(min_value=0, max_value=len(cs) - 1))
    spec.container = cs[ind]['name']

    # Just check it doesn't crash for now
    assert kubeyaml.find_container(spec, man) is not None
