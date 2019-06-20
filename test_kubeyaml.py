import kubeyaml

from hypothesis import given, note, assume, strategies as strats
from hypothesis import reproduce_failure, settings, HealthCheck
from hypothesis.strategies import composite
from ruamel.yaml.compat import StringIO
import string
import copy
import collections

def strip(s):
    return s.strip()

@composite
def image_components(draw):
    bits = draw(strats.lists(
        elements=strats.text(alphabet=alphanumerics, min_size=1),
        min_size=1, max_size=16))
    s = bits[0]
    for c in bits[1:]:
        sep = draw(image_separators)
        s = s + sep + c
    return s

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
    lambda h, p: str(h)+':'+str(p),
    hostnames_without_port, port_numbers)

hostnames = hostnames_without_port | hostnames_with_port

alphanumerics = strats.characters(min_codepoint=48, max_codepoint=122,
                                  whitelist_categories=['Ll', 'Lu', 'Nd'])
image_separators = strats.just('.')  | \
                   strats.just('_')  | \
                   strats.just('__') | \
                   strats.integers(min_value=1, max_value=5).map(lambda n: '-' * n)

host_segments = strats.just([]) | hostnames.map(lambda x: [x])

# This results in realistic image refs, we use a min_size of two for
# test cases that make use of a hostname as the Docker image ref spec
# has a limitation on images with a hostname that requires it to have
# at least two elements if the hostname is not localhost.
exact_image_names = strats.builds('/'.join, strats.lists(elements=image_components(), min_size=2, max_size=6))
# This is somewhat faster, if we don't care about having realistic
# image refs
sloppy_image_names = strats.text(string.ascii_letters + '-/_', min_size=1, max_size=255).map(strip)
sloppy_image_names_with_host = strats.builds(
    lambda host, name: host + '/' + name,
    hostnames, sloppy_image_names)
image_tags = strats.from_regex(r"^[a-z][\w.-]{0,127}$").map(strip)

# NB select the default image name format to use
image_names = sloppy_image_names | sloppy_image_names_with_host

images_with_tag = strats.builds(
    lambda name, tag: name + ':' + tag,
    image_names, image_tags)

# Kubernetes manifests
# https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.9/

controller_kinds = ['Deployment', 'DaemonSet', 'StatefulSet', 'CronJob']
custom_kinds = ['FluxHelmRelease', 'HelmRelease']
other_kinds = ['Service', 'ConfigMap', 'Secret']
# For checking against
workload_kinds = controller_kinds + custom_kinds
all_kinds = controller_kinds + custom_kinds + other_kinds
list_kinds = strats.sampled_from(all_kinds + ['']).map(lambda k: '%sList' % k)

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

def list_document(kind, resources):
    return {
        'kind': kind,
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
    initcontainer_names = draw(strats.sets(min_size=1, max_size=5, elements=dns_labels))
    assume(len(container_names & initcontainer_names)==0)
    containers = list(map(lambda n: container(n, draw(images_with_tag)), container_names))
    initcontainers = list(map(lambda n: container(n, draw(images_with_tag)), initcontainer_names))
    podtemplate = {'template': {'spec': {'containers': containers, 'initContainers': initcontainers}}}

    if base['kind'] == 'CronJob':
        base['spec'] = {'jobTemplate': {'spec': podtemplate}}
    else:
        base['spec'] = podtemplate

    return base

@given(controller_resources())
def test_includes_all_containers(man):
    spec = kubeyaml.podspec(man)
    assume(len(spec['initContainers']) > 0)
    for s in ['containers', 'initContainers']:
        for c in spec[s]:
            arg = Spec.from_resource(man)
            arg.container = c['name']
            assert kubeyaml.find_container(arg, man) is not None

## FluxHelmRelease interpretation

def destructive_merge(dict1, dict2):
    dict1.update(dict2)
    return dict1

def lift_containers(images):
    cs = [{k: images[k]['_containers'][0][kubeyaml.FHR_CONTAINER]} for k in images]
    images['_containers'] = cs
    return images

def combine_containers(toplevel, subfields):
    cs = []
    ims = {}
    if toplevel is not None:
        ims = toplevel
        cs = toplevel['_containers']
    cs = cs + subfields['_containers']
    destructive_merge(ims, subfields)
    ims['_containers'] = cs
    return ims

# These all return a dict specifying a "container" using an image,
# with an entry `'_containers'` saying what is being specified. E.g.,
# {'foo': {'image': 'foobar', 'tag': 'v1'}, '_containers': [{'foo': 'foobar:v1'}]}
image_only_values = (image_names | images_with_tag).map(lambda image: {'image': image, '_containers': [{kubeyaml.FHR_CONTAINER: image}]})
image_tag_values = strats.builds(lambda n, t: {'image': n, 'tag': t, '_containers': [{kubeyaml.FHR_CONTAINER: '%s:%s' % (n, t)}]}, image_names, image_tags)
image_registry_values = strats.builds(lambda r, n, t: {'registry': r, 'image': '%s:%s' % (n, t), '_containers': [{kubeyaml.FHR_CONTAINER: '%s/%s:%s' % (r, n, t)}]}, hostnames, exact_image_names, image_tags)
image_registry_tag_values = strats.builds(lambda r, n, t: {'registry': r, 'image': n, 'tag': t, '_containers': [{kubeyaml.FHR_CONTAINER: '%s/%s:%s' % (r, n, t)}]}, hostnames, exact_image_names, image_tags)
image_obj_values = strats.builds(lambda n, t: {'image': {'repository': n, 'tag': t}, '_containers': [{kubeyaml.FHR_CONTAINER: '%s:%s' % (n, t)}]}, image_names, image_tags)
image_obj_repository_values = (image_names | images_with_tag).map(lambda image: {'image': {'repository': image}, '_containers': [{kubeyaml.FHR_CONTAINER: image}]})
image_obj_registry_tag_values = strats.builds(lambda r, n, t: {'image': {'registry': r, 'repository': n, 'tag': t}, '_containers': [{kubeyaml.FHR_CONTAINER: '%s/%s:%s' % (r, n, t)}]}, hostnames, exact_image_names, image_tags)
image_obj_registry_repository_values = strats.builds(lambda r, n, t: {'image': {'registry': r, 'repository': '%s:%s' % (n, t)}, '_containers': [{kubeyaml.FHR_CONTAINER: '%s/%s:%s' % (r, n, t)}]}, hostnames, exact_image_names, image_tags)

# One of the above
toplevel_image_values = image_only_values | image_tag_values | image_registry_values | image_registry_tag_values | image_obj_values | image_obj_registry_tag_values | image_obj_registry_repository_values
# Some of the above, in fields
named_image_values = strats.dictionaries(keys=dns_labels, values=toplevel_image_values).map(lift_containers)
# Combo of top-level image, and images in subfields
all_image_values = strats.builds(combine_containers, strats.just(None) | toplevel_image_values, named_image_values)

values_noise = strats.deferred(lambda: strats.dictionaries(
    keys=dns_labels,
    values=values_noise | strats.integers() | strats.lists(values_noise) |
    strats.booleans() | strats.text(printable), max_size=3))

@settings(suppress_health_check=[HealthCheck.too_slow])
@given(all_image_values, values_noise)
def test_extract_custom_containers(image_values, noise):
    assume(len(set(image_values) & set(noise)) == 0)
    containers = image_values['_containers']
    custom_values = destructive_merge(image_values, noise)
    kind, ns, name = 'FluxHelmRelease', 'default', 'release'
    chart_name = 'chart'
    res = resource(kind, ns, name)
    res['spec'] = {
        'chartGitPath': chart_name,
        'values': custom_values,
    }

    original = {container: image for c in containers for container, image in c.items()}
    extracted = {c['name']: c['image'] for c in kubeyaml.containers(res)}
    assert original == extracted

@settings(suppress_health_check=[HealthCheck.too_slow])
@given(all_image_values, values_noise)
def test_set_custom_container_preserves_structure(image_values, noise):
    assume(len(set(image_values) & set(noise)) == 0)
    custom_values = destructive_merge(image_values, noise)
    original = copy.deepcopy(custom_values)

    kind, ns, name = 'FluxHelmRelease', 'default', 'release'
    chart_name = 'chart'
    res = resource(kind, ns, name)
    res['spec'] = {
        'chartGitPath': chart_name,
        'values': custom_values,
    }

    for c in image_values['_containers']:
        for name, image in c.items():
            # this should be an identity: set the container to what it was before
            kubeyaml.set_fluxhelmrelease_container(res, {'name': name}, image)
            assert original == res['spec']['values']

def custom_resource_values(values):
    return strats.builds(destructive_merge, values_noise, values)

@composite
def custom_resources(draw, image_values):
    kind, ns, name = draw(ids(custom_kinds))
    base = resource(kind, ns, name)
    chart_name = draw(dns_labels) # close enough
    base['spec'] = { # this is the spec for a FluxHelmRelease, but it's OK for HelmRelease too
        'chartGitPath': chart_name,
        'values': draw(custom_resource_values(image_values)),
    }
    return base

# --- /FluxHelmRelease interpretation

workload_resources = controller_resources() | custom_resources(all_image_values)
other_resources = strats.builds(resource_from_tuple, ids(other_kinds))

resources = workload_resources | other_resources
documents = resources | strats.builds(list_document, list_kinds, strats.lists(resources, max_size=6))

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

@settings(suppress_health_check=[HealthCheck.too_slow])
@given(workload_resources, strats.data())
def test_find_container(man, data):
    cs = kubeyaml.containers(man)
    assume(len(cs) > 0)

    spec = Spec.from_resource(man)
    ind = data.draw(strats.integers(min_value=0, max_value=len(cs) - 1))
    spec.container = cs[ind]['name']

    assert kubeyaml.find_container(spec, man) is not None

# Check that manifests() recurses into List resources
@given(documents)
def test_manifests(doc):
    for man in kubeyaml.manifests(doc):
        assert not man['kind'].endswith('List')

def check_structure(before, after):
    """A helper that checks whether the structure of two values differ, so
    we can see that e.g., setting the image doesn't upset which keys
    and other values are there.
    """
    if isinstance(before, collections.Mapping):
        assert isinstance(after, collections.Mapping)
        for k in before:
            assert k in after
            check_structure(before[k], after[k])
        for k in after:
            assert k in before
    else:
        assert not isinstance(after, collections.Mapping)

@given(workload_resources, images_with_tag | image_names, strats.data())
def test_image_update(man, image, data):
    cs = kubeyaml.containers(man)
    assume(len(cs) > 0)

    args = Spec.from_resource(man)
    args.image = image
    ind = data.draw(strats.integers(min_value=0, max_value=len(cs) - 1))
    args.container = cs[ind]['name']

    man1 = copy.deepcopy(man)
    man2 = None
    for out in kubeyaml.update_image(args, [man]):
        man2 = out

    assert man2 is not None
    assert kubeyaml.match_manifest(args, man2)
    outcs = kubeyaml.containers(man2)
    assert len(outcs) == len(cs)
    assert outcs[ind]['image'] == image
    check_structure(man1, man2)

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

@settings(suppress_health_check=[HealthCheck.too_slow])
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

@settings(suppress_health_check=[HealthCheck.too_slow])
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
