import kubeyaml

from hypothesis import given, note, assume, strategies as strats
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

# This is somewhat faster, if we don't care about having realistic
# image refs
sloppy_image_names = strats.text(string.ascii_letters + '-/_', min_size=1, max_size=255)
image_names = sloppy_image_names

images_with_tag = strats.builds(
    lambda name, tag: name + ':' + tag,
    image_names, image_tags)

# Kubernetes manifests
# https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.9/

controller_kinds = ['Deployment', 'DaemonSet', 'StatefulSet', 'CronJob']
custom_kinds = ['FluxHelmRelease']
other_kinds = ['Service', 'ConfigMap', 'Secret']
# For checking against
workload_kinds = controller_kinds + custom_kinds

namespaces = strats.just('') | dns_labels

def resource(kind, namespace, name):
    """Make a basic resource manifest, given the identifying fields.
    """
    metadata = {'name': name}
    if namespace != '':
        metadata['namespace'] = namespace

    return {
        'kind': kind,
        'metadata': metadata,
    }

def resource_from_tuple(t):
    k, ns, n = t
    return resource(k, ns, n)

def list_document(resources):
    return {
        'kind': 'List',
        'items': resources,
    }

def resource_id(man):
    """These three return values identify a resource uniquely"""
    kind = man['kind']
    ns = man['metadata'].get('namespace', 'default')
    name = man['metadata']['name']
    return kind, ns, name

def container(name, image):
    # TODO(michael): more to go here
    return dict(name=name, image=image)

def manifests_equiv(man1, man2):
    try:
        if man1['kind'] != man2['kind']: return False
        meta1, meta2 = man1['metadata'], man2['metadata']
        if meta1.get('namespace', None) != meta2.get('namespace', None): return False
        if meta1['name'] != meta2['name']: return False

        if man1['kind'] in workload_kinds:
            return containers_equal(kubeyaml.containers(man1), kubeyaml.containers(man2))
    except KeyError:
        return False

    return True

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

def ids(kinds):
    return strats.tuples(strats.sampled_from(kinds), namespaces, dns_labels)

controller_ids = ids(controller_kinds)

@composite
def controller_resources(draw):
    kind, ns, name = draw(controller_ids)
    base = resource(kind, ns, name)

    container_names = draw(strats.sets(min_size=1, max_size=5, elements=dns_labels))
    containers = list(map(lambda n: container(n, draw(images_with_tag)), container_names))
    podtemplate = {'template': {'spec': {'containers': containers}}}

    if base['kind'] == 'CronJob':
        base['spec'] = {'jobTemplate': {'spec': podtemplate}}
    else:
        base['spec'] = podtemplate

    return base

image_values = images_with_tag.map(lambda image: {'image': image})
named_images_values = strats.dictionaries(keys=dns_labels, values=image_values)

def destructive_merge(dict1, dict2):
    dict1.update(dict2)
    return dict1

values_noise = strats.deferred(lambda: strats.dictionaries(
    keys=dns_labels,
    values=values_noise | strats.integers() | strats.lists(values_noise) |
    strats.booleans() | strats.text(printable)))

custom_resource_values = strats.builds(destructive_merge, image_values | named_images_values, values_noise)

@composite
def custom_resources(draw):
    kind, ns, name = draw(ids(custom_kinds))
    base = resource(kind, ns, name)
    chart_name = draw(dns_labels) # close enough
    base['spec'] = {
        'chartGitPath': chart_name,
        'values': draw(custom_resource_values),
    }
    return base

workload_resources = controller_resources() | custom_resources()
other_resources = strats.builds(resource_from_tuple, ids(other_kinds))

resources = workload_resources | other_resources
documents = resources | strats.builds(list_document, strats.lists(resources, max_size=6))

class Spec:
    def __init__(self, kind=None, namespace=None, name=None):
        if kind is not None:
            self.kind = kind
        if namespace is not None:
            self.namespace = namespace
        if name is not None:
            self.name = name

    @staticmethod
    def from_resource(man):
        (kind, ns, name) = resource_id(man)
        return Spec(kind=kind, namespace=ns, name=name)

    def __repr__(self):
        return "Spec(kind=%s,name=%s,namespace=%s)" % (self.kind, self.name, self.namespace)

@given(workload_resources)
def test_match_self(man):
    spec = Spec.from_resource(man)
    assert kubeyaml.match_manifest(spec, man)

@given(workload_resources, strats.data())
def test_find_container(man, data):
    cs = kubeyaml.containers(man)
    assume(len(cs) > 0)

    spec = Spec.from_resource(man)
    ind = data.draw(strats.integers(min_value=0, max_value=len(cs) - 1))
    spec.container = cs[ind]['name']

    assert kubeyaml.find_container(spec, man) is not None

@given(documents)
def test_manifests(doc):
    for man in kubeyaml.manifests(doc):
        assert man['kind'] != 'List'

@given(workload_resources, images_with_tag, strats.data())
def test_image_update(man, image, data):
    cs = kubeyaml.containers(man)
    assume(len(cs) > 0)

    args = Spec.from_resource(man)
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

def comment_yaml(draw, yamlstr):
    """Serialise the values and add comments"""
    comments = strats.none() | \
               strats.text(printable).map(lambda s: '#' + s)
    res = ''
    prevLine = ''
    for line in yamlstr.splitlines():
        beforeLine = None
        endOfLine = None
        # special cases:
        #  - don't put comments before or on the same line as document delimiters
        if line == '---':
            pass
        #  - don't put a line comment before the first item in a list,
        #    because this breaks ruamel
        elif line.lstrip().startswith('-') and prevLine.find('-') != line.find('-'):
            endOfLine = draw(comments)
        #  - don't put a comment on the end of the first line of a map
        #    block (e.g., `'0':`), because guess what.
        elif line.endswith(':'):
            beforeLine = draw(comments)
        else:
            beforeLine = draw(comments)
            endOfLine = draw(comments)

        #  - don't put comments near an inline empty map,
        #    ruamel doesn't like that either
        if line.endswith('{}') or line.endswith(':'):
            beforeLine = None
            endOfLine = None

        if beforeLine is not None:
            res = res + beforeLine + '\n'
        res = res + line
        if endOfLine is not None:
            res = res + ' ' + endOfLine
        res = res + '\n'
        prevLine = line
    return res

@given(strats.lists(elements=documents, max_size=5), strats.data())
def test_ident_apply(mans, data):
    yaml = kubeyaml.yaml()
    original = StringIO()
    for man in mans:
        yaml.dump(man, original)
    note('Uncommented:\n%s\n' % original.getvalue())
    originalstr = comment_yaml(data.draw, original.getvalue())
    note('Commented:\n%s\n' % originalstr)
    infile = StringIO(originalstr)
    outfile = StringIO()

    def ident(docs):
        for d in docs:
            yield d

    kubeyaml.apply_to_yaml(ident, infile, outfile)
    note('Ruameled: \n%s\n' % outfile.getvalue())
    assert originalstr == outfile.getvalue()

@given(strats.lists(elements=documents, min_size=1, max_size=5), strats.data())
def test_update_image_apply(docs, data):
    originals = [man for doc in docs
                     for man in kubeyaml.manifests(doc)]
    workloads = [wl for wl in originals if wl['kind'] in workload_kinds]
    assume(len(workloads) > 0)
    # Make sure we got workloads with different IDs
    assume(len(workloads) == len(set(map(resource_id, workloads))))

    ind = data.draw(strats.integers(min_value=0, max_value=len(workloads)-1))
    workload = workloads[ind]
    containers = kubeyaml.containers(workload)
    assume(len(containers) > 0)

    yaml = kubeyaml.yaml()
    original = StringIO()
    for d in docs:
        yaml.dump(d, original)
    originalstr = comment_yaml(data.draw, original.getvalue())
    note('Original:\n%s\n' % originalstr)

    indc = data.draw(strats.integers(min_value=0, max_value=len(containers)-1))
    spec = Spec.from_resource(workload)
    spec.container = containers[indc]['name']
    spec.image = data.draw(images_with_tag)
    note('Spec: %r' % spec)

    infile, outfile = StringIO(originalstr), StringIO()
    kubeyaml.apply_to_yaml(lambda ds: kubeyaml.update_image(spec, ds), infile, outfile)

    # A rough check that the docs are in the same grouping into Lists,
    # since we'll look at individual manifests, ignoring whether they
    # are in Lists, after this.
    updateddocs = list(yaml.load_all(outfile.getvalue()))
    assert(len(docs) == len(updateddocs))
    for i in range(len(docs)):
        assert(updateddocs[i]['kind'] == docs[i]['kind'])

    # check that the selected manifest->container has the updated
    # image; and, the rest are unchanged.
    updateds = [man for doc in updateddocs for man in kubeyaml.manifests(doc)]
    assert(len(originals) == len(updateds))

    found = False
    for i in range(len(originals)):
        if kubeyaml.match_manifest(spec, updateds[i]):
            assert not found, "spec matched more than one manifest"
            c = kubeyaml.find_container(spec, updateds[i])
            assert c is not None
            assert c['image'] == spec.image
            found = True
        else:
            assert manifests_equiv(originals[i], updateds[i])
    assert found
