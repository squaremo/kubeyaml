import kubeyaml

from hypothesis import given, assume, strategies as strats
from hypothesis import reproduce_failure
from hypothesis.strategies import composite
from ruamel.yaml.compat import StringIO
import string

def strip(s):
    return s.strip()

# I only want things that will got on one line, so make my own printable alphabet
printable = string.ascii_letters + string.digits + string.punctuation + ' '

# Names of things in Kubernetes are generally DNS labels.
# https://github.com/kubernetes/community/blob/master/contributors/design-proposals/architecture/identifiers.md
dns_labels = strats.from_regex(
    r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$").map(strip)

# Image names (excluding digests for now)
# https://github.com/docker/distribution/blob/docker/1.13/reference/reference.go

host_components = strats.from_regex(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,125}[a-zA-Z0-9])?$").map(strip)
port_numbers = strats.integers(min_value=1, max_value=32767)

hostnames_without_port = strats.builds('.'.join,
    strats.lists(elements=host_components, min_size=1, max_size=6))

hostnames_with_port = strats.builds(
    lambda h, p: str(h, ':', p),
    hostnames_without_port, port_numbers)

hostnames = hostnames_without_port | hostnames_with_port

alphanumerics = strats.characters(min_codepoint=48, max_codepoint=122,
                                  whitelist_categories=['Ll', 'Lu', 'Nd'])
image_separators = strats.just('.')  | \
                   strats.just('_')  | \
                   strats.just('__') | \
                   strats.integers(min_value=1, max_value=5).map(lambda n: '-' * n)

image_tags = strats.from_regex(r"^[\w][\w.-]{0,127}$").map(strip)

@composite
def commented(draw, values):
    from ruamel.yaml.comments import CommentedMap
    comments = strats.none() | strats.text(printable, min_size=1)

    val = draw(values)
    if type(val) is dict:
        commentedval = CommentedMap()
        i = 0
        for k in val.keys():
            comment = draw(comments)
            commentedval.insert(i, k, val[k], comment=comment)
            i = i + 1
        val = commentedval
    return val

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

image_names = strats.builds(lambda cs: '/'.join(cs),
                            strats.lists(elements=image_components(),
                                         min_size=1, max_size=6))

images_with_tag = strats.builds(
    lambda name, tag: name + ':' + tag,
    image_names, image_tags)

# This is somewhat faster, if we don't care about having realistic
# image refs
sloppy_images_with_tag = strats.text(string.ascii_letters + '-/_', min_size=1, max_size=255)
images_with_tag = sloppy_images_with_tag

# Kubernetes manifests
# https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.9/

controller_kinds = ['Deployment', 'DaemonSet', 'StatefulSet', 'CronJob']

kinds = strats.sampled_from(controller_kinds)
namespaces = strats.just('') | dns_labels

def container(name, image):
    # TODO(michael): more to go here
    return dict(name=name, image=image)

def manifests_equal(man1, man2):
    try:
        if man1['kind'] != man2['kind']: return False
        meta1, meta2 = man1['metadata'], man2['metadata']
        if meta1.get('namespace', None) != meta2.get('namespace', None): return False
        if meta1['name'] != meta2['name']: return False
        return containers_equal(kubeyaml.containers(man1), kubeyaml.containers(man2))
    except KeyError:
        pass
    return False

def containers_equal(cs1, cs2):
    try:
        if len(cs1) != len(cs2): return False
        for c1, c2 in zip(cs1, cs2):
            if c1['name'] != c2['name']: return False
            if c2['image'] != c2['image']: return False
        return True
    except KeyError:
        pass
    return False

def manifest_id(man):
    """These three values identify a resource uniquely"""
    kind = man['kind']
    ns = man['metadata'].get('namespace', 'default')
    name = man['metadata']['name']
    return kind, ns, name

manifest_ids = strats.tuples(kinds, dns_labels, namespaces)

@composite
def manifests(draw):
    kind, name, namespace = draw(manifest_ids)
    metadata = dict()
    metadata['name'] = name
    if namespace != '':
        metadata['namespace'] = namespace

    container_names = draw(strats.sets(min_size=1, max_size=5, elements=dns_labels))
    containers = list(map(lambda n: container(n, draw(images_with_tag)), container_names))
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
    def __init__(self, kind=None, namespace=None, name=None):
        if kind is not None:
            self.kind = kind
        if namespace is not None:
            self.namespace = namespace
        if name is not None:
            self.name = name

    @staticmethod
    def from_manifest(man):
        (kind, ns, name) = manifest_id(man)
        return Spec(kind=kind, namespace=ns, name=name)

    def __repr__(self):
        return "Spec(kind=%s,name=%s,namespace=%s)" % (self.kind, self.name, self.namespace)

@given(manifests())
def test_match_self(man):
    spec = Spec.from_manifest(man)
    assert kubeyaml.match_manifest(spec, man)

@given(manifests(), strats.data())
def test_find_container(man, data):
    cs = kubeyaml.containers(man)
    assume(len(cs) > 0)

    spec = Spec.from_manifest(man)
    ind = data.draw(strats.integers(min_value=0, max_value=len(cs) - 1))
    spec.container = cs[ind]['name']

    assert kubeyaml.find_container(spec, man) is not None

@given(manifests(), images_with_tag, strats.data())
def test_image_update(man, image, data):
    cs = kubeyaml.containers(man)
    assume(len(cs) > 0)

    args = Spec.from_manifest(man)
    args.image = image
    ind = data.draw(strats.integers(min_value=0, max_value=len(cs) - 1))
    args.container = cs[ind]['name']

    found = False
    for out in kubeyaml.update_image(args, [man]):
        found = True
        assert(kubeyaml.match_manifest(args, out))
        outcs = kubeyaml.containers(out)
        assert(len(outcs) == len(cs))
        assert(outcs[ind]['image'] == image)
    assert(found)

@given(strats.lists(elements=commented(manifests()), max_size=5))
def test_ident_apply(mans):
    yaml = kubeyaml.yaml()
    original = StringIO()
    for man in mans:
        yaml.dump(man, original)
    infile = StringIO(original.getvalue())
    outfile = StringIO()

    def ident(docs):
        for d in docs:
            yield d

    kubeyaml.apply_to_yaml(ident, infile, outfile)
    assert original.getvalue() == outfile.getvalue()

@given(strats.lists(elements=commented(manifests()), min_size=1, max_size=5), strats.data())
def test_update_image_apply(mans, data):
    assume(len(mans) == len(set(map(manifest_id, mans))))

    ind = data.draw(strats.integers(min_value=0, max_value=len(mans)-1))

    yaml = kubeyaml.yaml()
    original = StringIO()
    for man in mans:
        yaml.dump(man, original)
    originalstr = original.getvalue()

    man = mans[ind]
    containers = kubeyaml.containers(man)
    indc = data.draw(strats.integers(min_value=0, max_value=len(containers)-1))
    spec = Spec.from_manifest(man)
    spec.container = containers[indc]['name']
    spec.image = data.draw(images_with_tag)

    infile, outfile = StringIO(originalstr), StringIO()
    kubeyaml.apply_to_yaml(lambda ds: kubeyaml.update_image(spec, ds), infile, outfile)

    updated = list(yaml.load_all(outfile.getvalue()))

    # the selected manifest->container has the updated image; the rest
    # are unchanged
    assert len(updated) == len(mans)
    found = False
    for i in range(len(mans)):
        if kubeyaml.match_manifest(spec, updated[i]):
            found = True
            c = kubeyaml.find_container(spec, updated[i])
            assert c is not None
            assert c['image'] == spec.image
        else:
            assert manifests_equal(mans[i], updated[i])
    assert found
