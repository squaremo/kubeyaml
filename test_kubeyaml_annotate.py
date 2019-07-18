import kubeyaml
from test_kubeyaml import resource, Spec

def test_update_annotations():
    man = resource('Deployment', 'default', 'foo')
    man['metadata']['annotations'] = {
        'fluxcd.io/automated': 'true',
        'fluxcd.io/locked': 'false',
    }

    automated = ['fluxcd.io/automated', 'false'] # should be altered
    unlocked = ['fluxcd.io/locked', ''] # should be removed
    new = ['fluxcd.io/tag.foo', 'glob:*'] # should be added

    args = Spec.from_resource(man)
    args.notes = [automated, unlocked, new]

    man1 = None
    for out in kubeyaml.update_annotations(args, [man]):
        man1 = out

    assert man1 is not None
    assert man1['metadata']['annotations'] == {
        automated[0]: automated[1],
        new[0]: new[1],
    }
