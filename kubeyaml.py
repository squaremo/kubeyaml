import sys
import argparse
import functools
import collections
from ruamel.yaml import YAML

# The container name, by proclamation, used for an image supplied in a
# FluxHelmRelease
FHR_CONTAINER = 'chart-image'

class NotFound(Exception):
    pass

def parse_args():
    p = argparse.ArgumentParser()
    subparsers = p.add_subparsers()

    image = subparsers.add_parser('image', help='update an image ref')
    image.add_argument('--namespace', required=True)
    image.add_argument('--kind', required=True)
    image.add_argument('--name', required=True)
    image.add_argument('--container', required=True)
    image.add_argument('--image', required=True)
    image.set_defaults(func=update_image)

    def note(s):
        k, v = s.split('=')
        return k, v

    annotation = subparsers.add_parser('annotate', help='update annotations')
    annotation.add_argument('--namespace', required=True)
    annotation.add_argument('--kind', required=True)
    annotation.add_argument('--name', required=True)
    annotation.add_argument('notes', nargs='+', type=note)
    annotation.set_defaults(func=update_annotations)

    return p.parse_args()

def yaml():
    y = YAML()
    y.explicit_start = True
    y.preserve_quotes = True
    return y

def bail(reason):
        sys.stderr.write(reason); sys.stderr.write('\n')
        sys.exit(2)

def apply_to_yaml(fn, infile, outfile):
    # fn :: iterator a -> iterator b
    y = yaml()
    docs = y.load_all(infile)
    for doc in fn(docs):
        y.dump(doc, outfile)

def update_image(args, docs):
    """Update the manifest specified by args, in the stream of docs"""
    found = False
    for doc in docs:
        if not found:
            for m in manifests(doc):
                c = find_container(args, m)
                if c != None:
                    set_container_image(m, c, args.image)
                    found = True
                    break
        yield doc
    if not found:
        raise NotFound()

def update_annotations(spec, docs):
    def ensure(d, *keys):
        for k in keys:
            try:
                d = d[k]
            except KeyError:
                d[k] = dict()
                d = d[k]
        return d

    found = False
    for doc in docs:
        if not found:
            for m in manifests(doc):
                if match_manifest(spec, m):
                    notes = ensure(m, 'metadata', 'annotations')
                    for k, v in spec.notes:
                        if v == '':
                            try:
                                del notes[k]
                            except KeyError:
                                pass
                        else:
                            notes[k] = v
                    if len(notes) == 0:
                        del m['metadata']['annotations']
                    found = True
                    break
        yield doc
    if not found:
        raise NotFound()

def manifests(doc):
    if doc['kind'] == 'List':
        for m in doc['items']:
            yield m
    else:
        yield doc

def match_manifest(spec, manifest):
    try:
        # NB treat the Kind as case-insensitive
        if manifest['kind'].lower() != spec.kind.lower():
            return False
        if manifest['metadata'].get('namespace', 'default') != spec.namespace:
            return False
        if manifest['metadata']['name'] != spec.name:
            return False
    except KeyError:
        return False
    return True

def containers(manifest):
    if manifest['kind'] == 'CronJob':
        return manifest['spec']['jobTemplate']['spec']['template']['spec']['containers']
    elif manifest['kind'] == 'FluxHelmRelease':
        return fluxhelmrelease_containers(manifest)
    return manifest['spec']['template']['spec']['containers']

def find_container(spec, manifest):
    if not match_manifest(spec, manifest):
        return None
    for c in containers(manifest):
        if c['name'] == spec.container:
            return c
    return None

def set_container_image(manifest, container, image):
    if manifest['kind'] == 'FluxHelmRelease':
        set_fluxhelmrelease_container(manifest, container, image)
    else:
        container['image'] = image

def mappings(values):
    return ((k, values[k]) for k in values if isinstance(values[k], collections.Mapping))

# There are different ways of interpreting FluxHelmRelease values as
# images, and we have to sniff to see which to use.
def fluxhelmrelease_containers(manifest):
    values = manifest['spec']['values']
    # Easiest one: the values section has a key called `image`, which
    # has the image used somewhere in the templates. Since we don't
    # know which container it appears in, it gets a standard name.
    if 'image' in values:
        return  [{
            'name': FHR_CONTAINER,
            'image': manifest['spec']['values']['image']
        }]
    # Second easiest: if there's at least one dict in values that has
    # a key `image`, then all such dicts are treated as containers,
    # named for their key.
    containers = []
    for k, v in mappings(values):
        if 'image' in v:
            containers.append({'name': k, 'image': v['image']})
    return containers

def set_fluxhelmrelease_container(manifest, container, image):
    values = manifest['spec']['values']
    if 'image' in values:
        values['image'] = image
        return
    for k, v in mappings(values):
        if k == container['name'] and 'image' in v:
            v['image'] = image
            return
    raise NotFound

def main():
    args = parse_args()
    try:
        apply_to_yaml(functools.partial(args.func, args), sys.stdin, sys.stdout)
    except NotFound:
        bail("manifest not found")

if __name__ == "__main__":
    main()
