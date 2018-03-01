import sys
import argparse
from ruamel.yaml import YAML

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--namespace')
    p.add_argument('--kind')
    p.add_argument('--name')
    p.add_argument('--container')
    p.add_argument('--image')
    return p.parse_args()

def bail(reason):
        print >> sys.stderr, reason
        sys.exit(2)

def main():
    args = parse_args()
    if args.image is None:
        bail("Argument --image required")

    yaml = YAML()
    yaml.explicit_start = True

    found = False
    for manifest in yaml.load_all(sys.stdin):
        c = find_container(args, manifest)
        if c != None:
            c['image'] = args.image
            found = True
        yaml.dump(manifest, sys.stdout)
    if not found:
        bail("Container not found")

def find_container(spec, manifest):
    if manifest['kind'] != spec.kind:
        return None
    if manifest['metadata']['name'] != spec.name:
        return None
    for c in manifest['spec']['template']['spec']['containers']:
        if c['name'] == spec.container:
            return c
    return None

if __name__ == "__main__":
    main()
