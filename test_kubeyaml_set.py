import copy
import kubeyaml
from test_kubeyaml import resource, Spec, check_structure

def test_set_paths():
    man = resource('HelmRelease', 'default', 'release')
    man['spec'] = {
        'chart': {
            'repository': 'https://kubernetes-charts.storage.googleapis.com/',
            'name': 'sample',
            'version': '0.1',
        },
        'values': {
            'image': {
                'repository': 'image/sample',
                'tag': '0.1',
            }
        }
    }

    image, tag = 'other/sample', '0.2'

    args = Spec.from_resource(man)
    args.paths = [['spec.values.image.tag', tag], ['spec.values.image.repository', image]]

    man1 = copy.deepcopy(man)
    man2 = None
    for out in kubeyaml.set_paths(args, [man]):
        man2 = out

    assert man2 is not None
    assert man2['spec']['values']['image']['repository'] == image
    assert man2['spec']['values']['image']['tag'] == tag
    check_structure(man1, man2)

def test_set_paths_raise_on_non_plain_values():
    man = resource('HelmRelease', 'default', 'release')
    man['spec'] = {
        'chart': {
            'repository': 'https://kubernetes-charts.storage.googleapis.com/',
        },
    }

    args = Spec.from_resource(man)
    args.paths = [['spec.chart', 'invalid']]

    man1 = copy.deepcopy(man)
    man2 = None

    try:
        for out in kubeyaml.set_paths(args, [man]):
            man2 = out
    except kubeyaml.UnresolvablePath:
        pass
    else:
        assert False, "UnresolvablePath not raised"

    assert man2 is not None
    assert man1 == man2
    check_structure(man1, man2)
