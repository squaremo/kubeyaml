import sys
import argparse
from ruamel.yaml import YAML

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
    return y

def bail(reason):
        sys.stderr.write(reason); sys.stderr.write('\n')
        sys.exit(2)

def apply_to_yaml_stream(fn, args):
    yaml = yaml()
    docs = yaml.load_all(sys.stdin)
    try:
        for doc in fn(args, docs):
            yaml.dump(doc, sys.stdout)
    except:
        bail("manifest not found")

def update_image(args, docs):
    """Update the manifest specified by args, in the stream of docs"""
    found = False
    for doc in docs:
        if not found:
            for m in manifests(doc):
                c = find_container(args, m)
                if c != None:
                    c['image'] = args.image
                    found = True
                    break
        yield doc
    if not found:
        raise "not found"

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
        raise "not found"

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
    return manifest['spec']['template']['spec']['containers']

def find_container(spec, manifest):
    if not match_manifest(spec, manifest):
        return None
    for c in containers(manifest):
        if c['name'] == spec.container:
            return c
    return None

def main():
    args = parse_args()
    apply_to_yaml_stream(args.func, args)

if __name__ == "__main__":
    main()
